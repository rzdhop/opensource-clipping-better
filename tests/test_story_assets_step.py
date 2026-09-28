"""The ``assets`` step and its image and voice regenerates (AI Story phase 4,
stage 8; spec 3 step 10, 6.4, 6.5, 8.1, 11; DEC-117, DEC-122, DEC-124,
DEC-135, DEC-151..155, DEC-160, DEC-165).

The story, the written script and the LLM stand-in are phase 3's
(``tests/test_story_episode_steps.py``); the TTS stand-ins are the
measurement's (``tests/test_story_measure.py``: ``tts.EDGE`` itself with its
``synthesize`` replaced). Images answer through a recording fake adapter,
STT through a recording fake transcriber. Offline and hermetic: no key,
chain, cap or limit of the machine reaches a test, no request leaves the
process, every default path is under ``tmp_path``, and ``data/`` and
``outputs/stories`` are fingerprinted before and after every test.

Stdlib + pytest (the CI environment, DEC-012). The step module is imported
inside the tests, so on the parent commit each test fails on its own instead
of the file failing to collect.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_story_episode_steps as eps
import test_story_measure as tsm
from clipping.aistory import schemas, steps, timing
from clipping.aistory.ledger import CostLedger
from clipping.cancel import Cancelled
from clipping.providers import generation
from clipping.providers.generation import GenResult
from clipping.providers.registry import Link

NOW = eps.NOW
KIWILO, MANGELLA, BROCCOLIA = eps.KIWILO, eps.MANGELLA, eps.BROCCOLIA
PARLOIR, PISCINE, PHONE = eps.PARLOIR, eps.PISCINE, eps.PHONE
VOICE_IDS = tsm.VOICE_IDS
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24

# Test values only: every request goes to a fake.
FREE = {"IMAGE_CHAIN": "pollinations/flux"}
FAL = {"FAL_KEY": "test-fal-key"}
PAID_IMAGES = {"IMAGE_CHAIN": "fal/flux-schnell", **FAL}
LOCAL_EDIT = {"IMAGE_EDIT_CHAIN": "local/comfyui"}
FAL_PRICE = 0.01
PORTRAIT_SEEDS = {KIWILO: 101, MANGELLA: 202, BROCCOLIA: 303}
PLATE_SEEDS = {PARLOIR: 404, PISCINE: 505}

GEN_VARS = (
    "FAL_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    "POLLINATIONS_API_KEY", "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN", "TTS_CHAIN", "VISION_CHAIN",
    "STT_CHAIN", "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL",
)


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    """Phase 3's rules, the image stack's and the TTS adapters': nothing of
    the machine reaches a test, nothing leaves it. The free tiers' pacing is
    lifted (the allowances still count), so two dozen images do not wait."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env (A-049)
    from clipping.providers import adapters, budget, images, limits, llm, local_comfyui, pacing, transport

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in eps.CHAIN_VARS + GEN_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LIMIT_POLLINATIONS_RPM", "0")
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

    def no_sdk(**_kwargs):
        raise AssertionError("the OpenAI SDK was reached")

    monkeypatch.setattr(transport, "urllib_transport", no_network)
    monkeypatch.setattr(images, "urllib_transport", no_network)
    monkeypatch.setattr(local_comfyui, "urllib_transport", no_network)
    monkeypatch.setattr(images, "_openai_client", no_sdk)

    before = {path: eps._fingerprint(path) for path in eps.REAL_FILES + eps.REAL_STORIES}
    yield
    assert limits.default_usage_path().startswith(str(tmp_path))
    assert budget.default_spend_path().startswith(str(tmp_path))
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    assert {path: eps._fingerprint(path) for path in eps.REAL_FILES + eps.REAL_STORIES} == before


@pytest.fixture
def store(tmp_path):
    return eps.StoryStore(tmp_path / "outputs", on_log=lambda line: None)


def _new():
    """The stage-8 modules (absent on the parent commit)."""
    from clipping.aistory import wordtiming
    from clipping.aistory.steps import assets, episode_regenerate, voice_lines

    return SimpleNamespace(assets=assets, regen=episode_regenerate, voice_lines=voice_lines,
                               wordtiming=wordtiming)


# ------------------------------------------------------------------ the fakes

class FakeImage:
    """An image adapter that records every probe and request and writes a
    small PNG whose bytes differ from one call to the next. *price* makes
    its estimate that many dollars (a paid link's); *on_generate(request)*
    runs before each answer."""

    def __init__(self, *, reachable=True, price=None, on_generate=None, fail_for=()):
        self.reachable = reachable
        self.price = price
        self.on_generate = on_generate
        self.fail_for = set(fail_for)
        self.probes = []
        self.requests = []

    def estimate(self, link, request):
        return self.price

    def probe(self, link, **_kwargs):
        self.probes.append(f"{link.provider}/{link.model}")
        return (True, "ok") if self.reachable else (False, "unreachable at http://comfy.test:8188")

    def generate(self, link, request, *, credentials, on_log, transport=None, **kwargs):
        self.requests.append(copy.copy(request))
        if self.on_generate is not None:
            self.on_generate(request, kwargs)
        if request.extra.get("name") in self.fail_for:
            raise RuntimeError(f"{link.provider}/{link.model}: synthetic failure")
        path = os.path.join(request.out_dir, f"{request.extra['name']}.png")
        with open(path, "wb") as fh:
            fh.write(PNG + str(len(self.requests)).encode())
        return GenResult(provider=link.provider, model=link.model, paths=(path,), seed=request.seed)

    def names(self):
        return [request.extra["name"] for request in self.requests]


class NeverImage(FakeImage):
    """Registered on the links a test says must never be called."""

    def generate(self, link, request, **_kwargs):
        raise AssertionError(f"{link.provider}/{link.model} must never be called")


class Transcriber:
    """The STT stand-in: every line's words as the script has them, 0.4 s
    each, or *answer(path)*; records every path."""

    def __init__(self, answer=None, *, by="groq/whisper-large-v3-turbo"):
        self.answer = answer
        self.by = by
        self.paths = []

    def __call__(self, path, *, language, on_log, cancel):
        self.paths.append(path)
        if self.answer is not None:
            return self.answer(path), self.by
        return [], self.by


def _adapters(edge=None, *, image=None, edit=None, fal=None, gemini=None):
    table = {("tts", "edge"): edge or tsm.Edge(), ("tts", "gemini"): gemini or tsm.NeverCalled(),
             ("tts", "local"): tsm.NeverCalled(),
             ("image", "pollinations"): image or NeverImage(), ("image", "fal"): fal or NeverImage(),
             ("image", "cloudflare"): NeverImage(), ("image", "openai"): NeverImage(),
             ("image", "local"): NeverImage(),
             ("image_edit", "local"): edit or NeverImage(), ("image_edit", "fal"): NeverImage(),
             ("image_edit", "gemini"): NeverImage()}
    return table


# ------------------------------------------------------------------ the story

def _media(tmp_path) -> Path:
    src = tmp_path / "ref.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0 reference")
    return src


def _episode(store, tmp_path, *, mode="prompt_only", seeds=True):
    """A ready story with its episode 1 written, planned (fast) and both
    documents approved; *seeds* gives each portrait and plate its own seed,
    references mode puts every reference image on disk."""
    m = eps._new()
    story_id = eps._ready_story(store)
    if mode != "prompt_only":
        store.update(story_id, lambda doc: doc["generation_profile"].update(consistency_mode=mode), now=NOW)
    if seeds:
        for cid, seed in PORTRAIT_SEEDS.items():
            doc = store.read_entity(story_id, "characters", cid)
            doc["refs"]["portrait"]["seed"] = seed
            store.write_entity(story_id, "characters", doc, now=NOW)
        for pid, seed in PLATE_SEEDS.items():
            doc = store.read_entity(story_id, "places", pid)
            doc["time_variants"]["day"]["seed"] = seed
            store.write_entity(story_id, "places", doc, now=NOW)
    if mode == "references":
        src = _media(tmp_path)
        for cid in (KIWILO, MANGELLA, BROCCOLIA):
            store.write_media(story_id, "characters", cid, "portrait.jpg", src)
        for pid in (PARLOIR, PISCINE):
            for name in ("variant_day.jpg", "variant_night.jpg"):
                store.write_media(story_id, "places", pid, name, src)
        store.write_media(story_id, "props", PHONE, "image.jpg", src)
    eps._run(m.script, store, story_id, llm=eps._script_llm())
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=eps.Log())
    _approve(store, story_id)
    return story_id


def _approve(store, story_id):
    script = eps._script(store, story_id)
    script["approved_at"] = NOW
    script["approved_anyway"] = NOW
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    board = eps._storyboard(store, story_id)
    board["approved_at"] = NOW
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)


def _settings(**extra):
    return {**eps.SETTINGS, **FREE, **extra}


def _ctx(store, story_id, *, params=None, settings=None):
    return eps._ctx(store, story_id, step="assets", params=params, settings=_settings() if settings is None
                    else settings)


def _run(store, story_id, *, adapters=None, params=None, settings=None, clock=None, transcribe=None, ctx=None):
    m = _new()
    if ctx is None:
        ctx, log = _ctx(store, story_id, params=params, settings=settings)
    else:
        log = ctx.on_log
    summary = m.assets.run(ctx, adapters=adapters or _adapters(), time_fn=clock or eps.Clock(0.0),
                           sleep_fn=lambda _s: None, transcribe=transcribe)
    return summary, log


def _failed(store, story_id, **kwargs):
    with pytest.raises(steps.StepFailed) as caught:
        _run(store, story_id, **kwargs)
    return str(caught.value)


def _board(store, story_id):
    return eps._storyboard(store, story_id)


def _shots(store, story_id):
    return _board(store, story_id)["shots"]


def _lines(script):
    return [line for scene in script["scenes"] for line in scene["lines"]]


def _ledger(store, story_id):
    path = Path(store.story_dir(story_id)) / "cost_ledger.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


def _episode_file(store, story_id, *parts):
    return Path(store.episode_dir(story_id, 1)).joinpath(*parts)


def _assets_doc(store, story_id):
    return store.read_episode_doc(story_id, 1, "assets.json")


def _cache_dir(store, story_id):
    return Path(store.story_dir(story_id)) / "cache" / "gen"


def _journal(store, story_id):
    """Every entry of the story's generation journal (not the kept answers beside them)."""
    entries = []
    for path in sorted(_cache_dir(store, story_id).glob("*.json")):
        if len(path.stem) == 64:
            entries.append(json.loads(path.read_text(encoding="utf-8")))
    return entries


# ================================================================ the whole run

def test_prompt_only_makes_every_shot_on_image_chain_labelled_with_no_reference(store, tmp_path):
    m = _new()
    story_id = _episode(store, tmp_path)
    story_before = eps._story_bytes(store, story_id)
    image, edit = FakeImage(), NeverImage()

    summary, log = _run(store, story_id, adapters=_adapters(image=image, edit=edit))

    board = _board(store, story_id)
    shots = board["shots"]
    # One request per distinct image: two shots asking for the very same
    # picture (same prompt, seed and size) share the cache's one answer.
    keys = list(dict.fromkeys(shot["assets"]["cache_key"] for shot in shots))
    assert len(image.requests) == len(keys) < len(shots) and edit.probes == []
    assert all(request.kind == "image" and request.references == () for request in image.requests)
    firsts = [next(shot for shot in shots if shot["assets"]["cache_key"] == key) for key in keys]
    assert image.names() == [f"shot_{shot['shot_id'][2:]}" for shot in firsts]
    requests = {shot["shot_id"]: request for shot, request in zip(firsts, image.requests)}
    for shot in shots:
        twin = next(first for first in firsts if first["assets"]["cache_key"] == shot["assets"]["cache_key"])
        request = requests[twin["shot_id"]]
        assets = shot["assets"]
        nn = shot["shot_id"][2:]
        assert assets["image"] == f"assets/shots/shot_{nn}.png"
        assert _episode_file(store, story_id, "assets", "shots", f"shot_{nn}.png").read_bytes().startswith(PNG)
        assert (assets["provider"], assets["model"], assets["consistency"], assets["route"]) == (
            "pollinations", "flux", "prompt_only", "free")
        assert assets["seed"] == request.seed and assets["est_usd"] == 0.0 and assets["pending"] is None
        assert assets["cache_key"] and len(assets["cache_key"]) == 64 and assets["generated_at"]
        assert request.prompt == shot["image_prompt"] and request.negative == shot["negative_prompt"]
        assert (request.width, request.height) == m.assets.SHOT_SIZE
        assert assets["prompt_hash"] == m.assets.prompt_hash(shot["image_prompt"], shot["negative_prompt"],
                                                             "prompt_only", m.assets.SHOT_SIZE, [])
        assert assets["approved"] is False and assets["video"] is None
    assert "🟡 Episode 1's shots: consistency: prompt-only (no reference image is sent)" in log
    assert sum(" consistency: prompt-only" in line and line.startswith("🖼 sh") for line in log) == len(shots)
    assert sum("(kept answer, no call)" in line for line in log) == len(shots) - len(keys)
    assert schemas.storyboard_errors(board) == []
    assert summary["complete"] is True and summary["failed"] == []
    assert summary["shots"]["made"] == len(keys) and summary["shots"]["cached"] == len(shots) - len(keys)
    assert set(summary["shots"]["states"].values()) == {"current"}
    assert log[-1] == "✅ Episode 1's assets are ready for your approval."
    # RC-E2: the story itself is never touched.
    assert eps._story_bytes(store, story_id) == story_before


def _first_character(shot):
    return next((tag[1:] for tag in shot["subject_tags"] if tag.startswith("@")), None)


def test_prompt_only_seeds_are_fixed_before_the_call_from_the_portrait_else_the_plate(store, tmp_path):
    from clipping.providers import gencache

    m = _new()
    story_id = _episode(store, tmp_path)
    script = eps._script(store, story_id)
    places = {scene["scene_id"]: scene["place_id"] for scene in script["scenes"]}
    seen = []

    def check(request, kwargs):
        # The seed is set, and the request journaled under the key it makes.
        assert isinstance(request.seed, int) and "on_submit" in kwargs
        seen.append(gencache.request_key("image", Link("pollinations", "flux"), request))

    image = FakeImage(on_generate=check)
    _run(store, story_id, adapters=_adapters(image=image))

    for shot in _shots(store, story_id):
        first = _first_character(shot)
        expected = PORTRAIT_SEEDS[first] if first else PLATE_SEEDS[places[shot["scene_id"]]]
        assert shot["assets"]["seed"] == expected, shot["shot_id"]
        assert shot["assets"]["cache_key"] in seen
    assert any(_first_character(shot) is None for shot in _shots(store, story_id))  # sh01: the phone alone
    assert m.assets.shot_seed(_shots(store, story_id)[0], script["scenes"][0], story_id=story_id, ep=1,
                              mode="prompt_only", entity_docs={"characters": {}, "places": {}}) == \
        m.assets.derive_seed(story_id, 1, "sh01")


def test_references_mode_checks_the_editor_first_then_sends_each_shots_references(store, tmp_path):
    m = _new()
    story_id = _episode(store, tmp_path, mode="references")
    edit, image = FakeImage(), NeverImage()

    summary, log = _run(store, story_id, adapters=_adapters(image=image, edit=edit), settings=_settings(**LOCAL_EDIT))

    shots = _shots(store, story_id)
    story_dir = Path(store.story_dir(story_id))
    assert len(edit.requests) == len(shots)  # a seed of its own per shot: no two requests are the same
    assert edit.probes[0] == "local/comfyui" and len(edit.probes) == 1 + len(shots)
    for shot, request in zip(shots, edit.requests):
        refs = shot["reference_images"][:4]
        assert request.kind == "image_edit" and refs
        assert list(request.references) == [os.path.realpath(story_dir / rel) for rel in refs]
        assert request.seed == m.assets.derive_seed(story_id, 1, shot["shot_id"]) == shot["assets"]["seed"]
        shas = [hashlib.sha256((story_dir / rel).read_bytes()).hexdigest() for rel in refs]
        assert shot["assets"]["prompt_hash"] == m.assets.prompt_hash(
            shot["image_prompt"], shot["negative_prompt"], "references", m.assets.SHOT_SIZE, shas)
        assert (shot["assets"]["consistency"], shot["assets"]["route"], shot["assets"]["provider"]) == (
            "references", "local", "local")
    assert not any("prompt-only" in line for line in log)
    assert summary["complete"] is True


def test_references_mode_with_no_editor_stops_and_asks_having_made_no_call(store, tmp_path):
    story_id = _episode(store, tmp_path, mode="references")
    board_before = _board(store, story_id)
    edge, edit = tsm.Edge(), FakeImage(reachable=False)

    message = _failed(store, story_id, adapters=_adapters(edge, edit=edit), settings=_settings(**LOCAL_EDIT))

    assert "Every shot of episode 1 needs an editor" in message and "Nothing was generated or spent" in message
    assert "unreachable at http://comfy.test:8188" in message and "prompt-only consistency" in message
    # Zero calls: not a line, not an image (only the editor's status probe).
    assert edge.calls == [] and edit.requests == [] and edit.probes == ["local/comfyui"]
    assert _ledger(store, story_id) == [] and _assets_doc(store, story_id) is None
    assert _board(store, story_id) == board_before
    assert not _cache_dir(store, story_id).exists() or os.listdir(_cache_dir(store, story_id)) == []


# ======================================================================= money

def _spent(store, story_id, usd, *, ep=1):
    """Earlier spending of episode *ep*, on the story's ledger."""
    CostLedger(str(Path(store.story_dir(story_id)) / "cost_ledger.json")).append(
        step="voice_measure", provider="gemini", model="x", unit="char", qty=10, est_usd=usd, paid=True, ep=ep)


def test_a_paid_plan_over_the_episode_cap_stops_before_the_first_call_with_the_numbers(store, tmp_path):
    story_id = _episode(store, tmp_path)
    shots = len(_shots(store, story_id))
    edge, fal = tsm.Edge(), FakeImage(price=FAL_PRICE)

    # Every image on the one paid link would be over the cap by itself.
    message = _failed(store, story_id, adapters=_adapters(edge, fal=fal),
                      settings=_settings(**PAID_IMAGES, ALLOW_PAID="1", PER_EPISODE_CAP_USD="0.05"))

    assert f"refused: est ${shots * FAL_PRICE:.3f} on fal/flux-schnell would bring this episode to " \
           f"${shots * FAL_PRICE:.2f} of its $0.05 cap" in message
    assert "Nothing was generated or spent" in message
    assert fal.requests == [] and edge.calls == [] and _ledger(store, story_id) == []

    # With what the episode spent already, the plan as a whole goes over.
    _spent(store, story_id, 0.9)
    message = _failed(store, story_id, adapters=_adapters(edge, fal=fal),
                      settings=_settings(IMAGE_CHAIN="fal/flux-schnell,pollinations/flux", **FAL, ALLOW_PAID="1"))

    total = 0.9 + shots * FAL_PRICE
    assert f"would bring this episode to ${total:.2f} of its $1.00 cap" in message
    assert "would go over a cap, so nothing was generated or spent" in message
    assert fal.requests == [] and edge.calls == [] and len(_ledger(store, story_id)) == 1
    assert not Path(os.environ["SPEND_PATH"]).exists()


def test_paid_images_within_the_caps_are_booked_once_each_on_the_episode(store, tmp_path):
    from clipping.providers import budget

    story_id = _episode(store, tmp_path)
    fal = FakeImage(price=FAL_PRICE)
    settings = _settings(**PAID_IMAGES, ALLOW_PAID="1")

    _summary, log = _run(store, story_id, adapters=_adapters(fal=fal), settings=settings)

    shots = _shots(store, story_id)
    images = [row for row in _ledger(store, story_id) if row["unit"] == "image"]
    assert len(images) == len(fal.requests) == len({shot["assets"]["cache_key"] for shot in shots})
    assert {(row["step"], row["ep"], row["provider"], row["model"], row["qty"], row["est_usd"], row["paid"])
            for row in images} == {("assets", 1, "fal", "fal-ai/flux/schnell", 1, FAL_PRICE, True)}
    assert all("note" not in row for row in images)
    assert budget.day_spent() == pytest.approx(FAL_PRICE * len(images))
    assert sum(line.startswith("   💸 fal/flux-schnell: est $0.010") for line in log) == len(images)
    assert all(shot["assets"]["route"] == "paid" and shot["assets"]["est_usd"] == FAL_PRICE for shot in shots)
    # Every paid request is journaled, done and stamped booked, once.
    paid = [entry for entry in _journal(store, story_id) if entry["paid"]]
    assert len(paid) == len(images) and all(entry["state"] == "done" and entry["booked"] for entry in paid)


def test_a_paid_or_keyless_link_is_never_contacted_without_allow_paid(store, tmp_path):
    story_id = _episode(store, tmp_path)
    image, fal = FakeImage(), NeverImage(price=FAL_PRICE)
    settings = _settings(IMAGE_CHAIN="openai/gpt-image-2-low,fal/flux-schnell,pollinations/flux", **FAL)

    _summary, log = _run(store, story_id, adapters=_adapters(image=image, fal=fal), settings=settings)

    assert fal.requests == [] and image.requests
    assert all(shot["assets"]["provider"] == "pollinations" for shot in _shots(store, story_id))
    assert any("Skipping openai/gpt-image-2-low: no API key" in line for line in log)
    assert any("Skipping fal/flux-schnell: paid link; allow_paid is off" in line for line in log)
    assert all(row["paid"] is False and row["est_usd"] == 0.0 for row in _ledger(store, story_id))
    assert not Path(os.environ["SPEND_PATH"]).exists()


def test_the_episode_cap_refuses_a_paid_image_at_the_call_with_the_numbers_and_only_this_episode_counts(
        store, tmp_path):
    m = _new()
    story_id = _episode(store, tmp_path)
    _run(store, story_id, adapters=_adapters(image=FakeImage()))
    ledger = CostLedger(str(Path(store.story_dir(story_id)) / "cost_ledger.json"))
    # Another episode's spending is not this episode's.
    ledger.append(step="assets", provider="fal", model="x", unit="image", qty=1, est_usd=5.0, paid=True, ep=2)
    fal = FakeImage(price=FAL_PRICE)
    settings = _settings(**PAID_IMAGES, ALLOW_PAID="1")

    first = _regenerate_shot(store, story_id, "sh05", adapters=_adapters(fal=fal), settings=settings)

    assert first["provider"] == "fal" and len(fal.requests) == 1
    rows = [row for row in _ledger(store, story_id) if row["provider"] == "fal" and row["ep"] == 1]
    assert [(row["step"], row["est_usd"], row["paid"]) for row in rows] == [("assets", FAL_PRICE, True)]
    # Now this episode is at the edge of its cap: the next paid image is refused before the call.
    ledger.append(step="assets", provider="fal", model="x", unit="image", qty=1, est_usd=0.985, paid=True, ep=1)
    with pytest.raises(steps.StepFailed) as caught:
        _regenerate_shot(store, story_id, "sh06", adapters=_adapters(fal=fal), settings=settings)
    message = str(caught.value)
    assert len(fal.requests) == 1
    assert "would bring this episode to $1.00" in message or "would bring this episode to $1.01" in message
    assert "of its $1.00 cap" in message and "sh06" not in message or "shot:1:sh06" in message
    shot = next(s for s in _shots(store, story_id) if s["shot_id"] == "sh06")
    assert shot["assets"]["pending"] is not None and shot["assets"]["provider"] == "pollinations"
    assert m.assets.shot_state(_ec(store, story_id), shot) == "failed"


# ====================================================================== voices

def test_voices_come_first_through_the_pinned_voices_alone_and_are_booked_on_the_episode(store, tmp_path,
                                                                                          monkeypatch):
    story_id = _episode(store, tmp_path)
    order = []
    real = generation.run_generation_chain

    def spy(kind, chain, request, **kwargs):
        order.append((kind, list(chain)))
        return real(kind, chain, request, **kwargs)

    monkeypatch.setattr(generation, "run_generation_chain", spy)
    edge, gemini = tsm.Edge(), tsm.NeverCalled()

    summary, log = _run(store, story_id, adapters=_adapters(edge, image=FakeImage(), gemini=gemini))

    script = eps._script(store, story_id)
    lines = _lines(script)
    tts = [chain for kind, chain in order if kind == "tts"]
    first_image = next(index for index, (kind, _chain) in enumerate(order) if kind == "image")
    assert all(kind == "tts" for kind, _chain in order[:first_image]) and len(tts) == len(lines)
    assert tts == [[Link("edge", VOICE_IDS[line["speaker"]])] for line in lines]
    assert gemini.calls == 0
    # A line spoken twice with the same words and voice is the same request:
    # the cache answers it the second time, and books it once.
    distinct = len({(line["text"], line["speaker"]) for line in lines})
    assert len(edge.calls) == distinct
    chars = [row for row in _ledger(store, story_id) if row["unit"] == "char"]
    assert len(chars) == distinct
    assert {(row["step"], row["ep"], row["provider"], row["paid"], row["est_usd"]) for row in chars} == {
        ("assets", 1, "edge", False, 0.0)}
    for line in lines:
        assert line["timing"]["source"] == "tts_word_timestamps" and line["timing"]["audio"].startswith(
            "assets/voice/line_")
    assert summary["lines"] == {"measured": len(lines), "unvoiced": []}
    assert script["timing"]["measured_lines"] == len(lines)


def test_a_failing_voice_fails_its_lines_alone_naming_the_alternate_voice_path(store, tmp_path, monkeypatch):
    story_id = _episode(store, tmp_path)
    tried = []
    real = generation.run_generation_chain

    def spy(kind, chain, request, **kwargs):
        if kind == "tts":
            tried.append(list(chain))
        return real(kind, chain, request, **kwargs)

    monkeypatch.setattr(generation, "run_generation_chain", spy)
    edge, gemini, image = tsm.Edge(failing={VOICE_IDS[BROCCOLIA]}), tsm.NeverCalled(), FakeImage()

    summary, log = _run(store, story_id, adapters=_adapters(edge, image=image, gemini=gemini))

    script = eps._script(store, story_id)
    hers = [line for line in _lines(script) if line["speaker"] == BROCCOLIA]
    assert [line["line_id"] for line in hers] == ["l13", "l17", "l28"]
    assert all(line["timing"]["source"] == "estimated" for line in hers)
    assert [chain for chain in tried if chain != [Link("edge", VOICE_IDS[KIWILO])]
            and chain != [Link("edge", VOICE_IDS[MANGELLA])]] == [[Link("edge", VOICE_IDS[BROCCOLIA])]] * 3
    assert gemini.calls == 0
    assert [item["target"] for item in summary["failed"]] == ["line:1:l13", "line:1:l17", "line:1:l28"]
    assert summary["complete"] is False and summary["lines"]["unvoiced"] == ["l13", "l17", "l28"]
    final = log[-1]
    assert final.startswith("⚠️ Episode 1's assets are not complete: ")
    assert "'line:1:l13', 'line:1:l17' and 'line:1:l28'" in final
    assert "pick another voice for Broccolia" in final and "Kiwilo" not in final.split("No other voice")[1]
    # Everything else was made: the images and the other lines.
    assert set(summary["shots"]["states"].values()) == {"current"}


def test_retiming_the_board_from_the_measured_lines_clears_no_approval(store, tmp_path):
    story_id = _episode(store, tmp_path)
    script_before, board_before = eps._script(store, story_id), _board(store, story_id)

    _run(store, story_id, adapters=_adapters(image=FakeImage()))

    script, board = eps._script(store, story_id), _board(store, story_id)
    assert (script["approved_at"], script["approved_anyway"], script["rev"]) == (NOW, NOW, script_before["rev"])
    assert script["consistency_report"] == script_before["consistency_report"]
    assert (board["approved_at"], board["rev"], board["scenes"], board["transitions"]) == (
        NOW, board_before["rev"], board_before["scenes"], board_before["transitions"])
    assert [shot["duration_s"] for shot in board["shots"]] != [shot["duration_s"] for shot in board_before["shots"]]
    assert script["timing"] == timing.episode_timing(script, eps.templates.load_episode_template("serial_60s_v1"),
                                                     "fr", style_lock=store.read_doc(story_id, "style_lock.json"),
                                                     storyboard=board)
    for old, new in zip(board_before["shots"], board["shots"]):
        assert {k: v for k, v in new.items() if k not in ("duration_s", "assets")} == \
            {k: v for k, v in old.items() if k not in ("duration_s", "assets")}


# ================================================================ word timings

def _her_words(store, story_id):
    """The STT stand-in's answer for a line file: the line's words, 0.4 s each."""
    def answer(path):
        line_id = "l" + Path(path).stem.split("_")[1]
        text = next(line["text"] for line in _lines(eps._script(store, story_id)) if line["line_id"] == line_id)
        return [{"word": word, "start": 0.4 * i, "end": 0.4 * i + 0.3} for i, word in enumerate(text.split())]
    return answer


def test_alignment_is_opt_in_asks_stt_only_for_lines_without_words_and_stores_the_source(store, tmp_path,
                                                                                         monkeypatch):
    from clipping.providers import tts

    monkeypatch.setattr(tts, "audio_duration", lambda path: 2.5)
    story_id = _episode(store, tmp_path)
    transcriber = Transcriber()
    edge = tsm.Edge(no_cues={VOICE_IDS[BROCCOLIA]})

    _run(store, story_id, adapters=_adapters(edge, image=FakeImage()), transcribe=transcriber)

    assert transcriber.paths == []
    lines = _assets_doc(store, story_id)["lines"]
    script = eps._script(store, story_id)
    for line in _lines(script):
        expected = "even_split" if line["speaker"] == BROCCOLIA else "provider"
        assert lines[line["line_id"]] == {"words_source": expected}

    transcriber = Transcriber(_her_words(store, story_id))
    summary, log = _run(store, story_id, adapters=_adapters(tsm.Edge(), image=NeverImage()),
                        params={"align_words": True}, transcribe=transcriber)

    assert sorted(Path(path).name for path in transcriber.paths) == ["line_13.mp3", "line_17.mp3", "line_28.mp3"]
    assert summary["aligned"] == ["l13", "l17", "l28"]
    lines = _assets_doc(store, story_id)["lines"]
    for line in _lines(script):
        if line["speaker"] == BROCCOLIA:
            assert lines[line["line_id"]] == {"words_source": "alignment", "aligned_by": "groq/whisper-large-v3-turbo"}
            sidecar = json.loads(_episode_file(store, story_id, "assets", "voice",
                                               f"line_{line['line_id'][1:]}.json").read_text(encoding="utf-8"))
            assert sidecar["words_source"] == "alignment" and sidecar["source"] == "audio_duration_only"
            assert [word["word"] for word in sidecar["words"]] == line["text"].split()
        else:
            assert lines[line["line_id"]] == {"words_source": "provider"}
    assert "🔤 l13: " in "\n".join(log)

    # Aligned once: a re-run asks the STT chain nothing.
    again = Transcriber(_her_words(store, story_id))
    _run(store, story_id, adapters=_adapters(tsm.Edge(), image=NeverImage()), params={"align_words": True},
         transcribe=again)
    assert again.paths == []


def test_a_line_the_stt_chain_cannot_align_keeps_the_even_split(store, tmp_path, monkeypatch):
    from clipping.providers import tts

    monkeypatch.setattr(tts, "audio_duration", lambda path: 2.5)
    story_id = _episode(store, tmp_path)

    def broken(path):
        raise RuntimeError("groq/whisper-large-v3-turbo: HTTP 503")

    summary, log = _run(store, story_id, adapters=_adapters(tsm.Edge(no_cues={VOICE_IDS[BROCCOLIA]}),
                                                            image=FakeImage()),
                        params={"align_words": True}, transcribe=Transcriber(broken))

    assert summary["aligned"] == [] and summary["failed"] == [] and summary["complete"] is True
    assert _assets_doc(store, story_id)["lines"]["l13"] == {"words_source": "even_split"}
    assert any(line.startswith("⚠️ l13: the words could not be aligned (groq/whisper-large-v3-turbo: HTTP 503)")
               for line in log)


# ================================================================== sfx and bgm

def test_sfx_and_bgm_are_resolved_into_assets_json_and_a_missing_cue_is_reported(store, tmp_path, monkeypatch):
    from clipping.aistory.render import audio_assets

    real = audio_assets.resolve_sfx
    monkeypatch.setattr(audio_assets, "resolve_sfx",
                        lambda pack, cue: None if cue == "gasp_crowd" else real(pack, cue))
    story_id = _episode(store, tmp_path)

    summary, log = _run(store, story_id, adapters=_adapters(image=FakeImage()))

    doc = _assets_doc(store, story_id)
    assert schemas.episode_assets_errors(doc) == [] and doc["$schema"] == "episode_assets_v1"
    assert doc["approved"] is None and doc["ep"] == 1
    script, board = eps._script(store, story_id), _board(store, story_id)
    template = eps.templates.load_episode_template("serial_60s_v1")
    lock = store.read_doc(story_id, "style_lock.json")
    result, _ = timing.episode_pass(script, template, "fr", style_lock=lock, storyboard=board)
    starts = timing.scene_starts(script, result, template, storyboard=board)
    offsets = timing.line_offsets(script, result, template, storyboard=board)
    cues = [(scene["scene_id"], cue) for scene in script["scenes"] for cue in scene["sfx_cues"]]
    assert len(doc["sfx"]) == len(cues) > 0
    for entry, (sid, cue) in zip(doc["sfx"], cues):
        assert (entry["scene_id"], entry["at"], entry["cue"], entry["pack"]) == (sid, cue["at"], cue["cue"], "soap")
        expected = starts[sid] if cue["at"] == "start" else offsets[cue["at"]][0]
        assert entry["offset_s"] == round(expected, 3)
        if cue["cue"] == "gasp_crowd":
            assert (entry["state"], entry["file"]) == ("missing", None)
        else:
            assert entry["state"] == "resolved" and entry["file"] == f"assets/sfx/soap/{cue['cue']}.wav"
            assert (eps.ROOT / entry["file"]).is_file()
    missing = sum(1 for entry in doc["sfx"] if entry["state"] == "missing")
    assert summary["sfx"] == {"resolved": len(cues) - missing, "missing": missing} and missing > 0
    assert any("SFX cue 'gasp_crowd'" in line and "skipped at render" in line for line in log)

    bgm = doc["bgm"]
    weights = {}
    for scene in script["scenes"]:
        seconds = result["scenes"][scene["scene_id"]]["duration_s"]
        weights[scene["emotion"]] = weights.get(scene["emotion"], 0.0) + seconds
    assert bgm["weights_s"] == {emotion: round(seconds, 3) for emotion, seconds in weights.items()}
    assert bgm["dominant_emotion"] == audio_assets.dominant_emotion(
        [{"emotion": s["emotion"], "duration_s": result["scenes"][s["scene_id"]]["duration_s"]}
         for s in script["scenes"]])
    assert bgm["mood"] == audio_assets.bgm_mood(lock["audio"], bgm["dominant_emotion"]) == summary["bgm"]
    track = audio_assets.pick_track(audio_assets.load_bgm_index(), bgm["mood"], story_id, 1)
    assert bgm["file"] == f"assets/bgm/{track['file']}" and bgm["licence"] == track["licence"]
    assert bgm["sha256"] == hashlib.sha256((eps.ROOT / bgm["file"]).read_bytes()).hexdigest()


def test_the_episode_ledger_view_is_written_with_this_episodes_rows_only(store, tmp_path):
    story_id = _episode(store, tmp_path)
    ledger = CostLedger(str(Path(store.story_dir(story_id)) / "cost_ledger.json"))
    ledger.append(step="assets", provider="fal", model="x", unit="image", qty=1, est_usd=0.5, paid=True, ep=2)

    _run(store, story_id, adapters=_adapters(image=FakeImage()))

    view = json.loads(_episode_file(store, story_id, "cost_ledger.json").read_text(encoding="utf-8"))
    rows = [row for row in _ledger(store, story_id) if row["ep"] == 1]
    assert view["$schema"] == "cost_ledger_v1" and view["ep"] == 1 and view["entries"] == rows and rows


def test_a_request_the_cache_cannot_key_is_booked_the_old_way_on_the_episode(store, tmp_path, monkeypatch):
    from clipping.providers import gencache

    monkeypatch.setattr(gencache.GenCache, "key", lambda self, kind, link, request: None)
    story_id = _episode(store, tmp_path)
    image = FakeImage()

    summary, _log = _run(store, story_id, adapters=_adapters(image=image))

    shots = _shots(store, story_id)
    # No key, no journal: every shot is its own call, each booked once by the step.
    assert len(image.requests) == len(shots) and _journal(store, story_id) == []
    images = [row for row in _ledger(store, story_id) if row["unit"] == "image"]
    assert len(images) == len(shots)
    assert {(row["step"], row["ep"], row["provider"], row["paid"]) for row in images} == {
        ("assets", 1, "pollinations", False)}
    assert all(shot["assets"]["cache_key"] is None for shot in shots) and summary["complete"] is True


# ============================================================ fill what is missing

def test_a_complete_re_run_makes_no_call_and_the_cache_serves_what_was_wiped(store, tmp_path):
    m = _new()
    story_id = _episode(store, tmp_path)
    _run(store, story_id, adapters=_adapters(image=FakeImage()))
    rows = _ledger(store, story_id)
    board = _board(store, story_id)
    edge, image = tsm.Edge(), FakeImage()

    summary, log = _run(store, story_id, adapters=_adapters(edge, image=image))

    assert edge.calls == [] and image.requests == [] and _ledger(store, story_id) == rows
    assert _board(store, story_id)["shots"] == board["shots"]
    assert "🖼 Every shot has its image (or is locked): nothing to make." in log
    assert "🎙 Every line is measured with its pinned voice: nothing to synthesise." in log
    assert summary["shots"]["made"] == summary["shots"]["cached"] == 0 and summary["complete"] is True

    # A storyboard that lost its image records, and files gone from disk:
    # the same requests again, answered by the cache -- no call, no booking.
    before = {shot["shot_id"]: shot["assets"]["cache_key"] for shot in board["shots"]}
    for shot in board["shots"]:
        shot["assets"] = {"image": None, "video": None, "seed": None, "provider": None, "approved": False}
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    for path in _episode_file(store, story_id, "assets", "shots").iterdir():
        path.unlink()
    _episode_file(store, story_id, "assets", "voice", "line_09.mp3").unlink()
    edge, image = tsm.Edge(), FakeImage()

    summary, log = _run(store, story_id, adapters=_adapters(edge, image=image))

    assert edge.calls == [] and image.requests == [] and _ledger(store, story_id) == rows
    shots = _shots(store, story_id)
    assert {shot["shot_id"]: shot["assets"]["cache_key"] for shot in shots} == before
    assert all(m.assets.shot_state(_ec(store, story_id), shot) == "current" for shot in shots)
    assert summary["shots"]["cached"] == len(shots) and summary["shots"]["made"] == 0
    assert _episode_file(store, story_id, "assets", "voice", "line_09.mp3").read_bytes() == b"ID3fake-mp3"
    assert any(line.startswith("   ♻️ edge/") for line in log)


def test_locked_shots_are_skipped_and_a_changed_prompt_makes_only_its_shot_stale(store, tmp_path):
    m = _new()
    story_id = _episode(store, tmp_path)
    _run(store, story_id, adapters=_adapters(image=FakeImage()))
    board = _board(store, story_id)
    by_id = {shot["shot_id"]: shot for shot in board["shots"]}
    by_id["sh05"]["prompt_override"] = "Mangella alone, lit from below, in the parlour."
    by_id["sh07"]["prompt_override"] = "A different close-up."
    by_id["sh07"]["assets"]["locked"] = True
    by_id["sh08"]["assets"]["locked"] = True
    by_id["sh08"]["assets"]["image"] = None
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    ec = _ec(store, story_id)
    states = {shot["shot_id"]: m.assets.shot_state(ec, shot) for shot in _shots(store, story_id)}
    assert (states["sh05"], states["sh07"], states["sh08"]) == ("stale", "locked_stale", "none")
    assert {state for sid, state in states.items() if sid not in ("sh05", "sh07", "sh08")} == {"current"}
    image = FakeImage()

    summary, _log = _run(store, story_id, adapters=_adapters(image=image))

    assert image.names() == ["shot_05"]
    # The note is not in the prompt; the user's override is, names removed (spec 2.3).
    assert image.requests[0].prompt == "the character alone, lit from below, in the parlour."
    after = {shot["shot_id"]: shot for shot in _shots(store, story_id)}
    assert m.assets.shot_state(ec, after["sh05"]) == "current"
    assert after["sh07"]["assets"] == by_id["sh07"]["assets"] and after["sh08"]["assets"]["image"] is None
    assert summary["shots"]["locked"] == 2 and summary["complete"] is True


# ================================================================== regenerate

def _ec(store, story_id):
    from clipping.aistory.steps import episode_common

    return episode_common.load_context(store, story_id, 1)


def _regenerate_shot(store, story_id, shot_id, *, note=None, adapters=None, settings=None):
    m = _new()
    ctx, _log = eps._ctx(store, story_id, step="regenerate", ep=None,
                         params={"target": f"shot:1:{shot_id}", "note": note},
                         settings=_settings() if settings is None else settings)
    return m.regen.run(ctx, f"shot:1:{shot_id}", ("shot_image", 1, shot_id), note, adapters=adapters or _adapters(),
                       sleep_fn=lambda _s: None, time_fn=eps.Clock(0.0))


def _regenerate_line(store, story_id, line_id, *, adapters=None, settings=None):
    m = _new()
    ctx, log = eps._ctx(store, story_id, step="regenerate", ep=None, params={"target": f"line:1:{line_id}"},
                        settings=_settings() if settings is None else settings)
    return m.regen.run(ctx, f"line:1:{line_id}", ("line", 1, line_id), None, adapters=adapters or _adapters(),
                       sleep_fn=lambda _s: None, time_fn=eps.Clock(0.0)), log


def test_a_shot_regenerate_persists_its_fresh_seed_and_note_before_the_call(store, tmp_path, monkeypatch):
    from clipping.aistory.steps import entities

    story_id = _episode(store, tmp_path)
    _run(store, story_id, adapters=_adapters(image=FakeImage()))
    before = _board(store, story_id)
    monkeypatch.setattr(entities, "fresh_seed", lambda: 4242)
    seen = []

    def on_disk(request, _kwargs):
        shot = next(s for s in _shots(store, story_id) if s["shot_id"] == "sh05")
        seen.append(dict(shot["assets"]["pending"]))

    image = FakeImage(on_generate=on_disk)

    result = _regenerate_shot(store, story_id, "sh05", note="Mangella glares at Kiwilo",
                              adapters=_adapters(image=image))

    assert image.names() == ["shot_05"] and image.requests[0].seed == 4242
    assert seen[0]["seed"] == 4242 and seen[0]["note"] == "Mangella glares at Kiwilo" and seen[0]["requested_at"]
    old = next(s for s in before["shots"] if s["shot_id"] == "sh05")
    assert image.requests[0].prompt == old["image_prompt"] + " Author's note: the character glares at the character."
    board = _board(store, story_id)
    shot = next(s for s in board["shots"] if s["shot_id"] == "sh05")
    assert (shot["assets"]["seed"], shot["assets"]["note"], shot["assets"]["pending"]) == (
        4242, "Mangella glares at Kiwilo", None)
    assert shot["assets"]["cache_key"] != old["assets"]["cache_key"]
    assert result == {"target": "shot:1:sh05", "shot": "sh05", "seed": 4242, "provider": "pollinations",
                      "cached": False}
    # Nothing else moved; no approval was cleared.
    assert board["approved_at"] == NOW and board["rev"] == before["rev"]
    assert [s for s in board["shots"] if s["shot_id"] != "sh05"] == [
        s for s in before["shots"] if s["shot_id"] != "sh05"]
    assert eps._script(store, story_id)["approved_at"] == NOW


def test_a_locked_shot_is_refused_before_any_call(store, tmp_path):
    story_id = _episode(store, tmp_path)
    board = _board(store, story_id)
    board["shots"][2]["assets"]["locked"] = True
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    image = FakeImage()

    with pytest.raises(steps.StepFailed) as caught:
        _regenerate_shot(store, story_id, "sh03", adapters=_adapters(image=image))

    assert "Cannot regenerate 'shot:1:sh03': shot sh03 is locked: unlock it first." == str(caught.value)
    assert image.requests == [] and _board(store, story_id) == board


def test_a_failed_regenerate_keeps_its_request_and_the_step_asks_for_the_same_image(store, tmp_path, monkeypatch):
    from clipping.aistory.steps import entities

    m = _new()
    story_id = _episode(store, tmp_path)
    _run(store, story_id, adapters=_adapters(image=FakeImage()))
    monkeypatch.setattr(entities, "fresh_seed", lambda: 777)
    failing = FakeImage(fail_for={"shot_05"})

    with pytest.raises(steps.StepFailed) as caught:
        _regenerate_shot(store, story_id, "sh05", note="darker", adapters=_adapters(image=failing))

    assert "synthetic failure" in str(caught.value) and "The request is kept (seed 777)" in str(caught.value)
    shot = next(s for s in _shots(store, story_id) if s["shot_id"] == "sh05")
    assert shot["assets"]["pending"]["seed"] == 777 and shot["assets"]["pending"]["note"] == "darker"
    assert m.assets.shot_state(_ec(store, story_id), shot) == "failed"
    image = FakeImage()

    summary, _log = _run(store, story_id, adapters=_adapters(image=image))

    assert image.names() == ["shot_05"] and image.requests[0].seed == 777
    assert image.requests[0].prompt == failing.requests[-1].prompt and image.requests[0].prompt.endswith(
        "Author's note: darker.")
    shot = next(s for s in _shots(store, story_id) if s["shot_id"] == "sh05")
    assert (shot["assets"]["pending"], shot["assets"]["seed"], shot["assets"]["note"]) == (None, 777, "darker")
    assert summary["complete"] is True


def test_a_line_regenerate_takes_its_pinned_voice_again_missing_the_cache_on_purpose(store, tmp_path):
    story_id = _episode(store, tmp_path)
    _run(store, story_id, adapters=_adapters(image=FakeImage()))
    rows = _ledger(store, story_id)
    board_before = _board(store, story_id)
    edge = tsm.Edge()

    result, log = _regenerate_line(store, story_id, "l13", adapters=_adapters(edge))

    line = next(ln for ln in _lines(eps._script(store, story_id)) if ln["line_id"] == "l13")
    assert [call["link"] for call in edge.calls] == [Link("edge", VOICE_IDS[BROCCOLIA])]
    assert edge.calls[0]["text"] == line["text"] and edge.calls[0]["extra"]["take"] == result["take"]
    new = _ledger(store, story_id)[len(rows):]
    assert [(row["step"], row["ep"], row["unit"], row["qty"]) for row in new] == [
        ("assets", 1, "char", len(line["text"]))]
    assert result["line"] == "l13" and result["voice"] == f"edge/{VOICE_IDS[BROCCOLIA]}"
    assert line["timing"]["audio"] == "assets/voice/line_13.mp3"
    script, board = eps._script(store, story_id), _board(store, story_id)
    assert script["approved_at"] == NOW and board["approved_at"] == NOW and board["rev"] == board_before["rev"]
    assert "🔁 Regenerated line:1:l13" in log


def test_a_line_regenerate_names_the_alternate_voice_path_when_its_voice_fails(store, tmp_path):
    story_id = _episode(store, tmp_path)
    edge, gemini = tsm.Edge(failing={VOICE_IDS[MANGELLA]}), tsm.NeverCalled()

    with pytest.raises(steps.StepFailed) as caught:
        _regenerate_line(store, story_id, "l09", adapters=_adapters(edge, gemini=gemini))

    message = str(caught.value)
    assert message.startswith("Cannot regenerate 'line:1:l09': ") and "pick another voice for Mangella" in message
    assert len(edge.calls) == 1 and gemini.calls == 0 and _ledger(store, story_id) == []


# ============================================================= budget, cancel, journal

def test_the_predictive_budget_stops_before_an_image_that_could_not_finish_naming_what_is_left(store, tmp_path):
    story_id = _episode(store, tmp_path)
    clock = eps.Clock(0.0)

    def slow(_request, _kwargs):
        clock.now += 600.0

    image = FakeImage(on_generate=slow)

    message = _failed(store, story_id, adapters=_adapters(image=image), clock=clock)

    # Images start at 0, 600 and 1200 (1200 + 300 fits in 1800); the 4th would
    # start at 1800 and could not finish: it is never started.
    assert image.names() == ["shot_01", "shot_02", "shot_03"]
    assert "30-minute" in message and "run the step again to continue" in message and "5 min" in message
    assert "Left: the images of shots sh04, sh05, " in message and "sh24." in message
    made = [shot["shot_id"] for shot in _shots(store, story_id) if shot["assets"]["image"]]
    assert made == ["sh01", "sh02", "sh03"]
    assert _episode_file(store, story_id, "cost_ledger.json").exists()

    again = FakeImage()
    _run(store, story_id, adapters=_adapters(image=again))
    assert "shot_01" not in again.names() and again.names()[0] == "shot_04"


def test_a_cancel_between_calls_keeps_what_was_made(store, tmp_path):
    story_id = _episode(store, tmp_path)
    ctx, _log = _ctx(store, story_id)

    def cancel_on_third(_request, _kwargs):
        if len(image.requests) == 3:
            ctx.cancel.cancel()

    image = FakeImage(on_generate=cancel_on_third)

    with pytest.raises(Cancelled):
        _run(store, story_id, adapters=_adapters(image=image), ctx=ctx)

    assert len(image.requests) == 3
    assert [shot["shot_id"] for shot in _shots(store, story_id) if shot["assets"]["image"]] == ["sh01", "sh02", "sh03"]
    assert _episode_file(store, story_id, "cost_ledger.json").exists()


def test_a_journal_that_cannot_book_a_submitted_request_stops_everything_naming_it(store, tmp_path, monkeypatch):
    story_id = _episode(store, tmp_path)

    def queued(_request, kwargs):
        kwargs["on_submit"]({"request_id": "req-777", "status_url": "https://queue.fal.run/status/req-777",
                             "response_url": "https://queue.fal.run/response/req-777"})

    fal = FakeImage(price=FAL_PRICE, on_generate=queued)
    real = CostLedger.append

    def refuse_images(self, **kwargs):
        if kwargs.get("unit") == "image":
            raise OSError("disk full")
        return real(self, **kwargs)

    monkeypatch.setattr(CostLedger, "append", refuse_images)
    settings = _settings(IMAGE_CHAIN="fal/flux-schnell,pollinations/flux", **FAL, ALLOW_PAID="1")
    image = NeverImage()

    message = _failed(store, story_id, adapters=_adapters(fal=fal, image=image), settings=settings)

    assert "req-777" in message and "https://queue.fal.run/status/req-777" in message
    assert "nothing else was tried" in message
    # One submit, then nothing: no other link, no other shot.
    assert len(fal.requests) == 1 and image.requests == []
    entry = next(entry for entry in _journal(store, story_id) if entry["paid"])
    assert entry["request"]["request_id"] == "req-777" and entry["state"] == "submitted"


# ================================================================ preconditions

@pytest.mark.parametrize("what,expected", [
    ("script", "Approve episode 1's script first"),
    ("storyboard", "Approve episode 1's storyboard first"),
    ("outdated", "shot prompts are outdated (Kiwilo changed since they were resolved)"),
])
def test_nothing_is_sent_before_the_script_and_a_current_storyboard_are_approved(store, tmp_path, what, expected):
    story_id = _episode(store, tmp_path)
    if what == "script":
        doc = eps._script(store, story_id)
        doc["approved_at"] = None
        store.write_episode_doc(story_id, 1, "script.json", doc, now=NOW)
    elif what == "storyboard":
        doc = _board(store, story_id)
        doc["approved_at"] = None
        store.write_episode_doc(story_id, 1, "storyboard.json", doc, now=NOW)
    else:
        doc = store.read_entity(story_id, "characters", KIWILO)
        store.write_entity(story_id, "characters", doc, now="2026-09-28T09:00:00+00:00")
    edge, image = tsm.Edge(), FakeImage()

    message = _failed(store, story_id, adapters=_adapters(edge, image=image))

    assert expected in message
    assert edge.calls == [] and image.requests == [] and _ledger(store, story_id) == []


# ================================================================== fingerprint

def test_the_fingerprint_follows_images_audio_and_locks_and_nothing_else(store, tmp_path):
    m = _new()
    story_id = _episode(store, tmp_path)
    _run(store, story_id, adapters=_adapters(image=FakeImage()))
    ec = _ec(store, story_id)
    script, board, doc = eps._script(store, story_id), _board(store, story_id), _assets_doc(store, story_id)
    base = m.assets.current_fingerprint(ec, board, script, doc)
    assert len(base) == 64

    moved = copy.deepcopy(board)
    moved["approved_at"], moved["updated_at"], moved["rev"] = None, "2026-10-01T00:00:00+00:00", 9
    for shot in moved["shots"]:
        shot["duration_s"] += 0.5
        shot["assets"]["generated_at"] = "2026-10-01T00:00:00+00:00"
    retimed = copy.deepcopy(script)
    retimed["approved_at"] = None
    for line in _lines(retimed):
        line["timing"]["duration_s"] += 1.0
    approved = dict(doc, approved={"at": NOW, "fingerprint": base}, updated_at="2026-10-01T00:00:00+00:00")
    assert m.assets.current_fingerprint(ec, moved, retimed, approved) == base

    locked = copy.deepcopy(board)
    locked["shots"][0]["assets"]["locked"] = True
    assert m.assets.current_fingerprint(ec, locked, script, doc) != base

    image = _episode_file(store, story_id, "assets", "shots", "shot_02.png")
    image.write_bytes(image.read_bytes() + b"retouched")
    changed_image = m.assets.current_fingerprint(ec, board, script, doc)
    assert changed_image != base

    audio = _episode_file(store, story_id, "assets", "voice", "line_09.mp3")
    audio.write_bytes(b"ID3another-take")
    assert m.assets.current_fingerprint(ec, board, script, doc) not in (base, changed_image)

    other_bgm = copy.deepcopy(doc)
    other_bgm["bgm"]["sha256"] = "0" * 64
    assert m.assets.current_fingerprint(ec, board, script, other_bgm) != m.assets.current_fingerprint(
        ec, board, script, doc)


def test_the_pure_fingerprint_takes_the_file_digests_it_is_given():
    from clipping.aistory.steps import assets

    board = {"shots": [{"shot_id": "sh01", "assets": {"prompt_hash": "a" * 64, "locked": False}}]}
    script = {"scenes": [{"lines": [{"line_id": "l01", "text": "Bonjour.", "timing": {"voice": "edge/x"}}]}]}
    doc = {"sfx": [], "bgm": None}
    one = assets.assets_fingerprint(board, script, doc, image_shas={"sh01": "1" * 64}, audio_shas={"l01": "2" * 64})
    assert one == assets.assets_fingerprint(board, script, doc, image_shas={"sh01": "1" * 64},
                                            audio_shas={"l01": "2" * 64})
    assert one != assets.assets_fingerprint(board, script, doc, image_shas={"sh01": "3" * 64},
                                            audio_shas={"l01": "2" * 64})
    assert one != assets.assets_fingerprint(board, script, doc, image_shas={"sh01": "1" * 64},
                                            audio_shas={"l01": None})


# ===================================================================== estimate

def test_the_estimate_counts_images_characters_alignment_and_caps_calling_nothing(store, tmp_path, monkeypatch):
    m = _new()
    story_id = _episode(store, tmp_path)
    ec = _ec(store, story_id)
    script, board = eps._script(store, story_id), _board(store, story_id)
    fal = NeverImage(price=FAL_PRICE)
    adapters = _adapters(fal=fal)
    settings = _settings(IMAGE_CHAIN="fal/flux-schnell,pollinations/flux", **FAL, ALLOW_PAID="1",
                         PER_EPISODE_CAP_USD="2.00")

    units = m.assets.asset_units(ec, script, board, env=settings, align_words=True, adapters=adapters)

    shots = len(board["shots"])
    lines = _lines(script)
    assert units["images"]["count"] == shots and units["images"]["shots"][0] == "sh01"
    assert (units["images"]["chain"], units["images"]["consistency"], units["images"]["route_class"],
            units["images"]["link"]) == ("IMAGE_CHAIN", "prompt_only", "paid", "fal/flux-schnell")
    assert units["images"]["est_usd"] == pytest.approx(shots * FAL_PRICE) == units["est_usd"]
    assert units["voices"]["lines"] == len(lines) and units["voices"]["chars"] == sum(len(ln["text"]) for ln in lines)
    assert units["alignment"] == {"opted_in": True, "requests": 0}  # Edge times its own words
    assert units["paid_links"] == [{"kind": "image", "link": "fal/flux-schnell", "allowed": True,
                                    "reason": "paid, allowed", "est_usd": pytest.approx(shots * FAL_PRICE)}]
    assert units["caps"]["allow_paid"] is True
    assert units["caps"]["episode"] == {"cap_usd": 2.0, "spent_usd": 0.0, "left_usd": 2.0}
    assert units["over_cap"] is None and units["ready"] is True
    assert fal.requests == [] and _ledger(store, story_id) == [] and not Path(os.environ["USAGE_PATH"]).exists()
    assert not _cache_dir(store, story_id).exists()

    # A cheaper cap: the paid link is not the one that runs, the free one is.
    capped = m.assets.asset_units(ec, script, board, env=dict(settings, PER_EPISODE_CAP_USD="0.05"), adapters=adapters)
    assert capped["ready"] is True and capped["images"]["route_class"] == "free" and capped["est_usd"] == 0.0
    assert capped["paid_links"][0]["allowed"] is False and "of its $0.05 cap" in capped["paid_links"][0]["reason"]
    # What the episode spent already counts against the plan as a whole.
    _spent(store, story_id, 1.9)
    over = m.assets.asset_units(ec, script, board, env=settings, adapters=adapters)
    total = 1.9 + shots * FAL_PRICE
    assert over["ready"] is False and f"would bring this episode to ${total:.2f} of its $2.00 cap" in over["over_cap"]
    assert over["caps"]["episode"] == {"cap_usd": 2.0, "spent_usd": 1.9, "left_usd": pytest.approx(0.1)}

    tsm._pin(store, story_id, MANGELLA, "gemini", "Kore")
    aligning = m.assets.asset_units(_ec(store, story_id), script, board, env=settings, align_words=True,
                                    adapters=adapters)
    assert aligning["alignment"] == {"opted_in": True, "requests": 5}  # Gemini times no word: her 5 lines

    off = m.assets.asset_units(ec, script, board, env=_settings(IMAGE_CHAIN="fal/flux-schnell", **FAL),
                               adapters=adapters)
    assert off["images"]["ready"] is False and off["ready"] is False and off["est_usd"] == 0.0
    assert off["paid_links"][0]["allowed"] is False and "allow_paid is off" in off["paid_links"][0]["reason"]
    assert off["alignment"] == {"opted_in": False, "requests": 0}


# ======================================================================= the lift

def test_the_script_step_speaks_its_lines_through_the_lifted_measurement():
    from clipping.aistory.steps import script, voice_lines

    assert issubclass(script._Run, voice_lines.LineMeasurement)
    for name in ("speaker_voice", "speaker_name", "is_measured", "lines_to_measure", "measure_estimate",
                 "asset_name", "no_voice_reason", "BudgetSpent", "STORY_TTS_CALL_SECONDS", "VOICE_ASSETS"):
        assert getattr(script, name) is getattr(voice_lines, name), name
    # The hooks ask for what phase 3 asked for: its ledger step, no cache, no take.
    assert script._Run.measure_step == "voice_measure" and script._Run.voice_take is None
    assert script._Run.voice_cache(None, None, None) is None


def test_the_assets_step_is_registered():
    assert "assets" in steps.RUNNERS and steps.RUNNERS["assets"].__name__ == "run_assets"


def test_the_real_transcriber_walks_the_keyed_hosted_links_of_stt_chain(monkeypatch):
    from clipping.providers import stt

    m = _new()
    assert m.assets.default_transcriber({}) == (None, "no hosted link of STT_CHAIN has a key")
    calls = []

    def fake(path, *, chain, keys, language, on_log, cancel):
        calls.append((path, [f"{link.provider}/{link.model}" for link in chain], sorted(keys), language))
        if chain[0].provider == "groq":
            raise stt.SttError("HTTP 503 from groq")
        return "", [{"start": 0.0, "end": 0.9, "words": [{"word": "Salut", "start": 0.1, "end": 0.5}]},
                    {"start": 0.9, "end": 1.4, "words": [{"word": "toi", "start": 0.9, "end": 1.3}]}], "fr"

    monkeypatch.setattr(stt, "transcribe", fake)
    transcribe, reason = m.assets.default_transcriber({"GROQ_API_KEY": "test-groq", "MISTRAL_API_KEY": "test-mistral"})

    assert reason is None
    words, by = transcribe("/scratch/line_05.mp3", language="fr", on_log=eps.Log(), cancel=None)
    assert words == [{"word": "Salut", "start": 0.1, "end": 0.5}, {"word": "toi", "start": 0.9, "end": 1.3}]
    assert by == "mistral/voxtral-mini-latest"
    assert [(path, chain, language) for path, chain, _keys, language in calls] == [
        ("/scratch/line_05.mp3", ["groq/whisper-large-v3-turbo"], "fr"),
        ("/scratch/line_05.mp3", ["mistral/voxtral-mini-latest"], "fr")]
    only_local, why = m.assets.default_transcriber({"STT_CHAIN": "local/faster-whisper", "GROQ_API_KEY": "x"})
    assert only_local is None and "no hosted link" in why
