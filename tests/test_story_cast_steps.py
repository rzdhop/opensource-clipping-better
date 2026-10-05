"""The phase-2 step runners: cast, places_proposal, places, season and the
entity targets of regenerate (AI Story phase 2, stage 6; spec 3 steps 5-7,
9.2, 11).

Every step writes into a real ``StoryStore`` under ``tmp_path``. The LLM is a
stand-in for ``llm.run_chain`` answering per prompt id from a queue and
recording each call; the image chains answer through fake adapters (a free
text-to-image link, an editor that is there or not); Edge TTS is a fake
adapter too. Offline and hermetic like ``test_story_refimages.py``: no key,
chain, cap or limit of the machine reaches a test, no request leaves the
process, and the repository's ``data/`` files and ``outputs/stories`` are
fingerprinted before and after every test.

Stdlib + pytest (the CI environment, DEC-012). The step modules are imported
inside the tests, so on the parent commit each test fails on its own instead
of the file failing to collect.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping.aistory import prompting, prompts, schemas, steps, stylelock, templates
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from clipping.providers.errors import ProviderError
from clipping.providers.generation import GenResult
from clipping.providers.registry import Link

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-26T10:00:00+00:00"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
LINK = Link("gemini", "gemini-test")

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
SETTINGS = {
    "LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key",
    "IMAGE_CHAIN": "pollinations/flux", "IMAGE_EDIT_CHAIN": "local/comfyui",
    "TTS_CHAIN": "edge/fr-FR-HenriNeural",
}
# fal without a key, allow_paid off: no link of IMAGE_EDIT_CHAIN can run.
NO_EDITOR = dict(SETTINGS, IMAGE_EDIT_CHAIN="fal/seedream-4-edit")

TENTAFRUIT = next(c for c in templates.load_concepts() if c["concept_id"] == "tentafruit_island")

KIWI = "an anthropomorphic kiwi with fuzzy brown skin and bright green flesh at the mouth"
MANGO = "an anthropomorphic mango with smooth orange-yellow skin and a sly grin"
FIG = "an anthropomorphic fig with soft purple skin and a tiny green stem"


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
    from clipping.providers import (adapters, budget, images, limits, llm, local_comfyui, pacing,
                                    transport)

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in GEN_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    # No pacing: a test makes more free images in a second than a minute allows.
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

    monkeypatch.setattr(images, "urllib_transport", no_network)
    monkeypatch.setattr(local_comfyui, "urllib_transport", no_network)
    monkeypatch.setattr(transport, "urllib_transport", no_network)
    monkeypatch.setattr(images, "_openai_client", no_sdk)

    before = {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES}
    yield
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    assert {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES} == before


@pytest.fixture
def store(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


# ------------------------------------------------------------------ helpers

def _new():
    """The stage-6 modules (absent on the parent commit)."""
    from clipping.aistory import refimages
    from clipping.aistory.steps import cast, entities, places, places_proposal, regenerate, season

    return SimpleNamespace(cast=cast, entities=entities, places=places, places_proposal=places_proposal,
                           regenerate=regenerate, season=season, refimages=refimages)


class Log(list):
    def __call__(self, line):
        self.append(str(line))


class FakeLLM:
    """Stands in for ``llm.run_chain``: answers each prompt id from its own
    queue, records every call as ``(prompt id, kwargs)``. An entry is a reply,
    an exception to raise, or ``f(call)`` returning either."""

    def __init__(self, **queues):
        self.queues = {prompt: list(replies) for prompt, replies in queues.items()}
        self.calls = []
        self._ids = {name: prompt for prompt, name in prompts.SCHEMA_NAMES.items()}

    def __call__(self, chain, **kwargs):
        prompt = self._ids[kwargs["schema_name"]]
        call = dict(kwargs, chain=list(chain), prompt=prompt)
        self.calls.append(call)
        queue = self.queues.get(prompt) or []
        if not queue:
            raise AssertionError(f"no {prompt} reply queued (call {len(self.calls)})")
        reply = queue.pop(0)
        if callable(reply) and not isinstance(reply, BaseException):
            reply = reply(call)
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply), LINK

    def of(self, prompt):
        return [call for call in self.calls if call["prompt"] == prompt]


class FakeImage:
    """An image adapter that records every probe and request and writes a
    small PNG whose bytes differ from one call to the next."""

    def __init__(self, *, reachable=True):
        self.reachable = reachable
        self.probes = []
        self.requests = []

    def estimate(self, link, request):
        return None

    def probe(self, link, **_kwargs):
        self.probes.append(f"{link.provider}/{link.model}")
        return (True, "ok") if self.reachable else (False, "unreachable (connection refused)")

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(copy.copy(request))
        path = os.path.join(request.out_dir, f"{request.extra['name']}.png")
        with open(path, "wb") as fh:
            fh.write(PNG + str(len(self.requests)).encode())
        return GenResult(provider=link.provider, model=link.model, paths=(path,), seed=request.seed, meta={})

    @property
    def calls(self):
        return len(self.probes) + len(self.requests)


class FakeTTS:
    """A free Edge stand-in: writes a small mp3, records every request."""

    def __init__(self):
        self.requests = []

    def estimate(self, link, request):
        return None

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append((f"{link.provider}/{link.model}", request.text))
        path = os.path.join(request.out_dir, "sample.mp3")
        with open(path, "wb") as fh:
            fh.write(b"ID3fake" + str(len(self.requests)).encode())
        return GenResult(provider=link.provider, model=link.model, paths=(path,), meta={"duration_s": 2.5})


class Fakes(SimpleNamespace):
    """The three fakes and the ``adapters`` map a step is handed."""

    @property
    def adapters(self):
        return {("image", "pollinations"): self.t2i, ("image_edit", "local"): self.editor,
                ("image_edit", "fal"): self.paid_editor, ("tts", "edge"): self.tts}

    def counts(self):
        """``(images, edits, paid-editor calls, samples)``: the requests each
        fake answered -- the paid editor's probes counted too."""
        return (len(self.t2i.requests), len(self.editor.requests), self.paid_editor.calls, len(self.tts.requests))


def _fakes(*, editor=True):
    return Fakes(t2i=FakeImage(), editor=FakeImage(reachable=editor), paid_editor=FakeImage(), tts=FakeTTS())


def _no_sleep(seconds):
    raise AssertionError(f"the chain tried to sleep {seconds}s")


def _lock():
    draft = stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=NOW)
    return stylelock.lock_style(draft, now=NOW)


def _story(store, *, mode="references", approve=("concept", "bible", "style")):
    """A French Tentafruit story: concept chosen, bible written, style locked,
    and the approvals in *approve* given."""
    story_id = store.create(language="fr", seed_text=None, style_template_id="fruit_drama",
                            now=NOW)["story_id"]
    concept = templates.localize_concept(TENTAFRUIT, "fr")

    def setup(doc):
        doc["concept_id"] = TENTAFRUIT["concept_id"]
        doc["concept"] = concept
        doc["title"] = concept["title"]
        doc["logline"] = concept["logline"]
        doc["premise"] = "Chaque semaine, un couple est éliminé. Le coco-téléphone annonce le vote."
        doc["tone"] = "mélodramatique, rapide"
        doc["generation_profile"]["consistency_mode"] = mode
        for key in approve:
            doc["approvals"][key] = NOW

    store.update(story_id, setup, now=NOW)
    store.write_doc(story_id, "style_lock.json", _lock(), now=NOW, validator=schemas.style_lock_errors)
    return story_id


def _ctx(store, story_id, *, step, params=None, settings=None):
    log = Log()
    ctx = steps.StepContext(
        job_id="job000000001", story_id=story_id, step=step, ep=None, params=params or {},
        cancel=CancelToken(), settings_env=dict(SETTINGS if settings is None else settings),
        outputs_dir=store.outputs_dir, on_log=log,
    )
    return ctx, log


def _run(module, store, story_id, *, step, llm, fakes, params=None, settings=None):
    """Run *module*'s step; ``(summary, log)``."""
    ctx, log = _ctx(store, story_id, step=step, params=params, settings=settings)
    kwargs = {"runner": llm, "time_fn": lambda: 100.0}
    if step != "season" and step != "places_proposal":
        kwargs.update(sleep_fn=_no_sleep, adapters=fakes.adapters if fakes else None)
    return module.run(ctx, **kwargs), log


def _failed(module, store, story_id, *, step, llm, fakes, params=None, settings=None):
    """Run a step expected to fail; ``(message, log)``."""
    ctx, log = _ctx(store, story_id, step=step, params=params, settings=settings)
    kwargs = {"runner": llm, "time_fn": lambda: 100.0}
    if step != "season" and step != "places_proposal":
        kwargs.update(sleep_fn=_no_sleep, adapters=fakes.adapters if fakes else None)
    with pytest.raises(steps.StepFailed) as caught:
        module.run(ctx, **kwargs)
    return str(caught.value), log


def k1(descriptor, items, *, gender="male", age="adult", tags=("warm",), sample="Je gagne toujours, mon cœur.",
       relationships=()):
    return {
        "descriptor": descriptor,
        "signature_items": list(items),
        "personality": {"traits": ["rusé", "charmeur"], "wants": "Gagner le vote de la semaine.",
                        "fears": "Être démasqué devant tout le monde.", "speech_style": "Des phrases courtes."},
        "voice": {"gender": gender, "age": age, "style_tags": list(tags), "direction": "smug, warm, a little nasal",
                  "sample_line": sample},
        "relationships": [{"with": name, "relation": relation} for name, relation in relationships],
    }


K1_KIWI = k1(KIWI, ["thin gold chain", "white linen shirt", "left-eyebrow scar"])
K1_MANGO = k1(MANGO, ["rhinestone crown hair clip", "red satin dress"], gender="female",
              sample="Je ne perds jamais, chéri.", relationships=[("Kiwilo", "son ex secret")])
K1_FIG = k1(FIG, ["round glasses", "yellow cardigan"], gender="female", age="young", tags=("bright",),
            sample="Oh non, pas encore un vote !", relationships=[("Broccolia", "sa patronne")])

CAST_PARAMS = {"selected": ["Kiwilo", "Mangella"],
               "custom": [{"name": "Figuette", "role": "support", "one_line": "Une figue timide qui voit tout."}]}
IDS = ["char_kiwilo", "char_mangella", "char_figuette"]


def _cast_llm(*extra):
    return FakeLLM(K1=[K1_KIWI, K1_MANGO, K1_FIG, *extra])


def _cast(store, story_id, fakes, *, params=CAST_PARAMS, settings=None, llm=None):
    m = _new()
    llm = llm or _cast_llm()
    summary, log = _run(m.cast, store, story_id, step="cast", llm=llm, fakes=fakes, params=params,
                        settings=settings)
    return summary, log, llm


def _chars(store, story_id):
    return {doc["char_id"]: doc for doc in store.list_entities(story_id, "characters")}


def _entity_bytes(store, story_id, kind):
    folder = Path(store.story_dir(story_id)) / kind
    return {path.relative_to(folder).as_posix(): path.read_bytes()
            for path in sorted(folder.rglob("*")) if path.is_file()}


def _approve_cast(store, story_id):
    for doc in store.list_entities(story_id, "characters"):
        doc["approved_at"] = NOW
        store.write_entity(story_id, "characters", doc, now=NOW)
    assert store.get(story_id)["approvals"]["cast"]


# ======================================================================= cast

def test_cast_creates_only_the_selected_sketch_characters_and_a_custom_one(store):
    story_id = _story(store)
    fakes = _fakes()

    summary, log, llm = _cast(store, story_id, fakes)

    chars = _chars(store, story_id)
    assert sorted(chars) == sorted(IDS)
    assert [(chars[cid]["name"], chars[cid]["role"], chars[cid]["source"]) for cid in IDS] == [
        ("Kiwilo", "lead", "sketch"), ("Mangella", "lead", "sketch"), ("Figuette", "support", "custom")]
    assert chars["char_kiwilo"]["archetype"] == "manipulateur charmeur"
    assert chars["char_figuette"]["archetype"] == ""
    assert store.get(story_id)["cast_ids"] == IDS
    assert summary["created"] == IDS and summary["written"] == IDS
    # One K1 per character, one portrait each, two sheets each from the editor, a voice and a sample each.
    assert len(llm.of("K1")) == 3
    assert fakes.counts() == (3, 6, 0, 3)
    assert summary["images"] == {cid: {"portrait": "base", "turnaround": "references",
                                       "expressions": "references"} for cid in IDS}
    assert summary["needs_editor"] == [] and summary["pick_voice"] == []
    assert summary["samples"] == {cid: "voice_sample.mp3" for cid in IDS}
    assert "👤 Kiwilo: portrait" in log and "👤 Figuette: voice sample" in log
    assert sum(line.startswith("✍️ K1 via gemini/gemini-test") for line in log) == 3
    for cid in IDS:
        refs = chars[cid]["refs"]
        assert (refs["portrait"]["name"], refs["turnaround"]["name"], refs["expressions"]["name"]) == (
            "portrait.png", "turnaround.png", "expressions.png")
        assert chars[cid]["approved_at"] is None


def test_k1_sees_the_cast_so_far_and_writes_every_field_with_relationships_as_ids(store):
    story_id = _story(store)
    summary, log, llm = _cast(store, story_id, _fakes())

    first, second, third = (call["user"] for call in llm.of("K1"))
    assert "Existing cast:" not in first
    assert "Character to write: Kiwilo (lead, manipulateur charmeur)" in first
    assert "Signature hint: une fine chaîne en or" in first
    assert "Existing cast:\n- Kiwilo (lead): " in second and "— looks: an anthropomorphic kiwi" in second
    assert "Character to write: Figuette (support)\nOne line: Une figue timide qui voit tout.\n\n" in third
    assert "Signature hint" not in third
    assert all(call["max_tokens"] == 750 for call in llm.of("K1"))

    lock = store.read_doc(story_id, "style_lock.json")
    chars = _chars(store, story_id)
    mango = chars["char_mangella"]
    assert mango["descriptor"] == MANGO
    assert mango["signature_items"] == ["rhinestone crown hair clip", "red satin dress"]
    assert mango["personality"]["traits"] == ["rusé", "charmeur"]
    assert mango["relationships"] == {"char_kiwilo": "son ex secret"}
    assert mango["voice_hints"] == {"gender": "female", "age": "adult", "style_tags": ["warm"],
                                    "direction": "smug, warm, a little nasal",
                                    "sample_line": "Je ne perds jamais, chéri."}
    assert mango["prompt_block"] == prompting.character_prompt_block(
        lock, descriptor=MANGO, signature_items=["rhinestone crown hair clip", "red satin dress"])
    # Broccolia is in the sketch but not in the cast: the relationship is dropped, and said.
    assert chars["char_figuette"]["relationships"] == {}
    assert "ℹ️ Figuette: relationship with 'Broccolia' dropped -- no character of that name." in log


def test_k1_receives_the_upload_notes_and_an_upload_added_meanwhile_is_kept(store):
    m = _new()
    story_id = _story(store)
    doc = m.cast.new_character("char_kiwilo", "Kiwilo", "lead", "Un kiwi charmeur.", archetype=None,
                               source="sketch", now=NOW)
    doc["refs"]["uploads"] = [{"name": "0" * 31 + "1.png", "description": "a green kiwi in a white shirt",
                               "uploaded_at": NOW}]
    store.write_entity(story_id, "characters", doc, now=NOW)

    def reply(call):
        # A second design reference lands while K1 is out.
        current = store.read_entity(story_id, "characters", "char_kiwilo")
        current["refs"]["uploads"].append({"name": "0" * 31 + "2.png", "description": None, "uploaded_at": NOW})
        store.write_entity(story_id, "characters", current, now=NOW)
        return K1_KIWI

    summary, _log, llm = _cast(store, story_id, _fakes(), params={}, llm=FakeLLM(K1=[reply]))

    [call] = llm.of("K1")
    assert ("Design reference supplied by the author: a green kiwi in a white shirt; follow it.\n\n"
            in call["user"])
    # A sketch character keeps its sketch's hint, even when it was created earlier.
    assert "Signature hint: une fine chaîne en or" in call["user"]
    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    assert kiwi["descriptor"] == KIWI
    assert [entry["name"] for entry in kiwi["refs"]["uploads"]] == ["0" * 31 + "1.png", "0" * 31 + "2.png"]
    assert summary["created"] == [] and summary["written"] == ["char_kiwilo"]


def test_with_no_editor_every_character_waits_with_zero_edit_calls_and_the_step_returns(store):
    story_id = _story(store)
    fakes = _fakes()
    profile = store.get(story_id)["generation_profile"]

    summary, log, _llm = _cast(store, story_id, fakes, settings=NO_EDITOR)

    # The portraits are made; no editor was contacted in any way; voices and samples still made.
    assert fakes.counts() == (3, 0, 0, 3) and fakes.editor.calls == 0
    assert [item["char_id"] for item in summary["needs_editor"]] == IDS
    message = summary["needs_editor"][0]["message"]
    assert "fal/seedream-4-edit" in message
    assert (f"🟡 Needs a reference-capable editor or prompt-only consistency for: Kiwilo, Mangella, "
            f"Figuette — {message}") in log
    assert sum(line.startswith("✋ ") for line in log) == 3  # one stop per character, not per sheet
    for doc in _chars(store, story_id).values():
        assert doc["refs"]["portrait"] is not None
        assert (doc["refs"]["turnaround"], doc["refs"]["expressions"]) == (None, None)
    assert store.get(story_id)["generation_profile"] == profile  # never switched here
    assert summary["images"] == {cid: {"portrait": "base"} for cid in IDS}


def test_prompt_only_then_fills_the_sheets_without_redoing_text_portraits_or_voices(store):
    story_id = _story(store)
    fakes = _fakes()
    _cast(store, story_id, fakes, settings=NO_EDITOR)
    before = _chars(store, story_id)

    def switch(doc):
        doc["generation_profile"]["consistency_mode"] = "prompt_only"

    store.update(story_id, switch, now=NOW)
    llm = FakeLLM()
    summary, log, _ = _cast(store, story_id, fakes, params={}, settings=NO_EDITOR, llm=llm)

    assert llm.calls == []
    # Six sheets from text alone; no portrait again; no editor; no new sample.
    assert fakes.counts() == (3 + 6, 0, 0, 3)
    assert [request.kind for request in fakes.t2i.requests[3:]] == ["image"] * 6
    after = _chars(store, story_id)
    for cid in IDS:
        assert after[cid]["refs"]["portrait"] == before[cid]["refs"]["portrait"]
        assert after[cid]["descriptor"] == before[cid]["descriptor"]
        assert after[cid]["voice"] == before[cid]["voice"]
        assert after[cid]["refs"]["turnaround"]["consistency"] == "prompt_only"
        assert after[cid]["refs"]["expressions"]["consistency"] == "prompt_only"
        # Made from the portrait's seed.
        assert after[cid]["refs"]["turnaround"]["seed"] == before[cid]["refs"]["portrait"]["seed"]
    assert summary["written"] == [] and summary["created"] == []
    assert summary["images"] == {cid: {"turnaround": "prompt_only", "expressions": "prompt_only"} for cid in IDS}
    assert summary["needs_editor"] == [] and summary["voices"] == {} and summary["samples"] == {}
    assert log.count("🟡 Kiwilo turnaround: consistency: prompt-only") == 1


def test_three_distinct_voices_are_pinned_from_the_briefs_and_samples_written(store):
    story_id = _story(store)
    fakes = _fakes()

    summary, _log, _llm = _cast(store, story_id, fakes)

    chars = _chars(store, story_id)
    pinned = {cid: (chars[cid]["voice"]["provider"], chars[cid]["voice"]["voice_id"]) for cid in IDS}
    assert pinned == {"char_kiwilo": ("edge", "fr-FR-HenriNeural"),
                      "char_mangella": ("edge", "fr-FR-DeniseNeural"),
                      "char_figuette": ("edge", "fr-FR-EloiseNeural")}
    assert summary["voices"] == {cid: f"{p}/{v}" for cid, (p, v) in pinned.items()}
    assert chars["char_mangella"]["voice"]["sample_line"] == "Je ne perds jamais, chéri."
    assert chars["char_mangella"]["voice"]["direction"] == "smug, warm, a little nasal"
    # Each sample through its own voice alone, speaking its own line.
    assert fakes.tts.requests == [
        ("edge/fr-FR-HenriNeural", "Je gagne toujours, mon cœur."),
        ("edge/fr-FR-DeniseNeural", "Je ne perds jamais, chéri."),
        ("edge/fr-FR-EloiseNeural", "Oh non, pas encore un vote !"),
    ]
    for cid in IDS:
        assert os.path.isfile(store.media_path(story_id, "characters", cid, "voice_sample.mp3"))


def test_an_existing_pin_is_taken_and_an_existing_name_is_only_included(store):
    story_id = _story(store)
    fakes = _fakes()
    _cast(store, story_id, fakes, params={"selected": ["Kiwilo"]}, llm=FakeLLM(K1=[K1_KIWI]))

    pepper = k1("an anthropomorphic red pepper in a loud shirt", ["heart-shaped sunglasses", "gold hoop"])
    summary, _log, llm = _cast(store, story_id, fakes, params={"selected": ["Kiwilo", "Pepperino"]},
                               llm=FakeLLM(K1=[pepper]))

    assert summary["created"] == ["char_pepperino"] and summary["written"] == ["char_pepperino"]
    assert len(llm.of("K1")) == 1
    chars = _chars(store, story_id)
    assert sorted(chars) == ["char_kiwilo", "char_pepperino"]
    # Kiwilo keeps Henri; Pepperino, also a man and an adult, gets the next best one.
    assert chars["char_kiwilo"]["voice"]["voice_id"] == "fr-FR-HenriNeural"
    assert chars["char_pepperino"]["voice"]["voice_id"] == "fr-FR-RemyMultilingualNeural"


def test_a_k1_failure_for_one_character_does_not_stop_the_others(store):
    story_id = _story(store)
    fakes = _fakes()
    leaky = k1("a kiwi called Kiwilo with fuzzy brown skin", ["thin gold chain", "white linen shirt"])
    llm = FakeLLM(K1=[leaky, leaky, K1_MANGO, K1_FIG])

    message, log = _failed(_new().cast, store, story_id, step="cast", llm=llm, fakes=fakes, params=CAST_PARAMS)

    assert message.startswith("Cast incomplete: Kiwilo text failed (the reply failed validation twice: "
                              "$.descriptor: must not mention the character's own name)")
    assert message.endswith("Run the cast step again to fill what is missing, or regenerate "
                            "'character:char_kiwilo:text'.")
    chars = _chars(store, story_id)
    assert chars["char_kiwilo"]["descriptor"] is None and chars["char_kiwilo"]["refs"]["portrait"] is None
    assert chars["char_kiwilo"]["voice"] is None
    for cid in ("char_mangella", "char_figuette"):
        assert chars[cid]["descriptor"] and chars[cid]["refs"]["expressions"] is not None
        assert chars[cid]["voice"] is not None
    assert fakes.counts() == (2, 4, 0, 2)
    assert any(line.startswith("✖ Kiwilo text failed: ") for line in log)
    # Mangella's relationship to the unwritten Kiwilo still maps: the character exists.
    assert chars["char_mangella"]["relationships"] == {"char_kiwilo": "son ex secret"}


def test_a_character_deleted_while_the_step_runs_is_skipped(store):
    story_id = _story(store)
    fakes = _fakes()

    def kiwi_then_delete_mangella(call):
        store.delete_entity(story_id, "characters", "char_mangella", now=NOW)
        return K1_KIWI

    summary, log, llm = _cast(store, story_id, fakes, llm=FakeLLM(K1=[kiwi_then_delete_mangella, K1_FIG]))

    assert "ℹ️ Mangella was removed while the step ran; skipped." in log
    assert sorted(_chars(store, story_id)) == ["char_figuette", "char_kiwilo"]
    assert summary["written"] == ["char_kiwilo", "char_figuette"]
    assert len(llm.of("K1")) == 2 and fakes.counts() == (2, 4, 0, 2)


def test_an_image_or_sample_failure_is_local_and_named(store):
    story_id = _story(store)
    fakes = _fakes()

    class Broken(FakeTTS):
        def generate(self, link, request, **kwargs):
            if "Denise" in link.model:
                raise ProviderError("edge: synthetic failure")
            return super().generate(link, request, **kwargs)

    fakes.tts = Broken()
    message, _log = _failed(_new().cast, store, story_id, step="cast", llm=_cast_llm(), fakes=fakes,
                            params=CAST_PARAMS)

    assert "Mangella voice sample failed (Mangella: edge/fr-FR-DeniseNeural could not make the sample" in message
    assert "'character:char_mangella:voice'" in message
    assert "Other voices: edge/" in message
    assert [text for _link, text in fakes.tts.requests] == ["Je gagne toujours, mon cœur.",
                                                             "Oh non, pas encore un vote !"]


def test_rerunning_with_nothing_missing_calls_nothing_and_writes_nothing(store):
    story_id = _story(store)
    fakes = _fakes()
    _cast(store, story_id, fakes)
    counts = fakes.counts()
    before = _entity_bytes(store, story_id, "characters")
    story_before = store.get(story_id)

    summary, _log, llm = _cast(store, story_id, fakes, params={}, llm=FakeLLM())

    assert llm.calls == [] and fakes.counts() == counts
    assert _entity_bytes(store, story_id, "characters") == before
    assert store.get(story_id)["updated_at"] == story_before["updated_at"]
    assert summary == {"created": [], "written": [], "images": {}, "needs_editor": [], "voices": {},
                       "pick_voice": [], "samples": {}}


def test_filling_a_missing_image_clears_that_characters_approval(store):
    story_id = _story(store)
    fakes = _fakes()
    _cast(store, story_id, fakes, settings=NO_EDITOR)
    _approve_cast(store, story_id)

    summary, _log, _llm = _cast(store, story_id, fakes, params={}, llm=FakeLLM())  # an editor now

    assert summary["images"] == {cid: {"turnaround": "references", "expressions": "references"} for cid in IDS}
    assert all(doc["approved_at"] is None for doc in _chars(store, story_id).values())
    assert store.get(story_id)["approvals"]["cast"] is None


def test_the_cast_step_checks_before_anything(store):
    m = _new()
    fakes = _fakes()
    unapproved = _story(store, approve=("concept", "bible"))
    message, _ = _failed(m.cast, store, unapproved, step="cast", llm=FakeLLM(), fakes=fakes, params=CAST_PARAMS)
    assert message == "Approve the style first."
    # A style approval over a cleared bible approval does not count.
    stale = _story(store, approve=("concept", "style"))
    message, _ = _failed(m.cast, store, stale, step="cast", llm=FakeLLM(), fakes=fakes, params=CAST_PARAMS)
    assert message == "Approve the style first."

    story_id = _story(store)
    message, _ = _failed(m.cast, store, story_id, step="cast", llm=FakeLLM(), fakes=fakes,
                         params={"selected": ["Kiwilo", "Zorglub"]})
    assert message == ("'Zorglub' is not in the concept's cast sketch; pick from 'Kiwilo', 'Mangella', "
                       "'Broccolia', 'Pepperino' and 'Avocardo'.")
    message, _ = _failed(m.cast, store, story_id, step="cast", llm=FakeLLM(), fakes=fakes,
                         params={"custom": [{"name": "Figuette", "role": "villain", "one_line": "x"}]})
    assert message.startswith("Figuette cannot be created: $.role")
    message, _ = _failed(m.cast, store, story_id, step="cast", llm=FakeLLM(), fakes=fakes, params={})
    assert message == "Pick characters from the concept's cast sketch or add your own first."
    assert store.list_entities(story_id, "characters") == []
    assert fakes.counts() == (0, 0, 0, 0)


# ============================================================ places & props

P0_REPLY = {
    "places": [{"name": "Le camp de plage", "one_line": "Là où les couples dorment et complotent."},
               {"name": "Le feu d'élimination", "one_line": "Là où tombe le verdict du vote."}],
    "props": [{"name": "Le coco-téléphone", "one_line": "Il annonce le résultat du vote.", "owner": "Kiwilo"}],
}
P1_BEACH = {"descriptor": "a crescent of white sand with palm-leaf huts and a stone bonfire ring",
            "layout_notes": "huts on the left, the sea on the right, the bonfire ring at the back",
            "time_variants": ["day", "night"]}
P1_FIRE = {"descriptor": "a ring of tiki torches around a pit of glowing embers under palm trees",
           "layout_notes": "torches in a circle, the jury bench at the back",
           "time_variants": ["day"]}
R1_PHONE = {"descriptor": "a hollow coconut with a curly cord and a brass dial", "owner": "Kiwilo"}
PLACES_PARAMS = {"places": P0_REPLY["places"],
                 "props": [{"name": "Le coco-téléphone", "one_line": "Il annonce le vote.", "owner": None}]}


def _with_cast(store, fakes=None, **story_kwargs):
    story_id = _story(store, **story_kwargs)
    _cast(store, story_id, fakes or _fakes())
    return story_id


def test_places_proposal_writes_the_proposal_with_owner_ids(store):
    m = _new()
    story_id = _with_cast(store)
    llm = FakeLLM(P0=[P0_REPLY])

    summary, log = _run(m.places_proposal, store, story_id, step="places_proposal", llm=llm, fakes=None)

    [call] = llm.of("P0")
    assert ("Existing cast:\n- Kiwilo — signature: thin gold chain, white linen shirt, left-eyebrow scar"
            in call["user"])
    doc = store.read_doc(story_id, "places_proposal.json")
    assert doc["$schema"] == "places_proposal_v1"
    assert doc["places"] == P0_REPLY["places"]
    assert doc["props"] == [{"name": "Le coco-téléphone", "one_line": "Il annonce le résultat du vote.",
                             "owner": "char_kiwilo"}]
    assert summary == {"places": doc["places"], "props": doc["props"]}
    assert "🗺 Proposed 2 place(s): Le camp de plage · Le feu d'élimination; 1 prop(s): Le coco-téléphone" in log


def test_places_proposal_needs_the_style_and_a_cast(store):
    m = _new()
    story_id = _story(store)
    message, _ = _failed(m.places_proposal, store, story_id, step="places_proposal", llm=FakeLLM(), fakes=None)
    assert message == "Write the cast first: places and props are proposed from it."
    unapproved = _story(store, approve=("concept", "bible"))
    message, _ = _failed(m.places_proposal, store, unapproved, step="places_proposal", llm=FakeLLM(), fakes=None)
    assert message == "Approve the style first."


def test_places_creates_two_places_and_one_prop_with_day_plates_a_prop_image_and_no_variant(store):
    m = _new()
    fakes = _fakes()
    story_id = _with_cast(store, fakes)
    counts = fakes.counts()
    llm = FakeLLM(P1=[P1_BEACH, P1_FIRE], R1=[R1_PHONE])

    summary, log = _run(m.places, store, story_id, step="places", llm=llm, fakes=fakes, params=PLACES_PARAMS)

    assert [len(llm.of(p)) for p in ("P1", "R1")] == [2, 1]
    assert "Existing places:" not in llm.of("P1")[0]["user"]
    assert "Existing places:\n- Le camp de plage: " in llm.of("P1")[1]["user"]
    # Two master plates and one prop image, all text to image; no variant, no editor.
    t2i, editor, paid, tts = fakes.counts()
    assert (t2i - counts[0], editor - counts[1], paid, tts - counts[3]) == (3, 0, 0, 0)
    assert [r.extra["name"] for r in fakes.t2i.requests[counts[0]:]] == ["variant_day", "variant_day", "image"]
    lock = store.read_doc(story_id, "style_lock.json")
    beach = store.read_entity(story_id, "places", "place_le_camp_de_plage")
    assert beach["descriptor"] == P1_BEACH["descriptor"]
    assert list(beach["time_variants"]) == ["day", "night"]
    assert beach["time_variants"]["night"] is None
    assert beach["time_variants"]["day"]["consistency"] == "base"
    assert beach["prompt_block"] == prompting.place_prompt_block(
        lock, descriptor=P1_BEACH["descriptor"], layout_notes=P1_BEACH["layout_notes"])
    fire = store.read_entity(story_id, "places", "place_le_feu_d_elimination")
    assert list(fire["time_variants"]) == ["day"]
    phone = store.read_entity(story_id, "props", "prop_le_coco_telephone")
    assert phone["owner_char_id"] == "char_kiwilo"  # R1's owner, none having been given
    assert phone["image"]["name"] == "image.png"
    assert phone["prompt_block"] == prompting.prop_prompt_block(lock, descriptor=R1_PHONE["descriptor"])
    assert summary == {
        "created": ["place_le_camp_de_plage", "place_le_feu_d_elimination", "prop_le_coco_telephone"],
        "written": ["place_le_camp_de_plage", "place_le_feu_d_elimination", "prop_le_coco_telephone"],
        "images": {"place_le_camp_de_plage": {"day": "base"}, "place_le_feu_d_elimination": {"day": "base"},
                   "prop_le_coco_telephone": {"image": "base"}},
    }

    # Nothing missing: nothing called, nothing written.
    before = _entity_bytes(store, story_id, "places"), _entity_bytes(store, story_id, "props")
    again, _ = _run(m.places, store, story_id, step="places", llm=FakeLLM(), fakes=fakes, params=PLACES_PARAMS)
    assert again == {"created": [], "written": [], "images": {}}
    assert (_entity_bytes(store, story_id, "places"), _entity_bytes(store, story_id, "props")) == before


def test_places_uses_the_saved_proposal_and_asks_for_one_without_it(store):
    m = _new()
    fakes = _fakes()
    story_id = _with_cast(store, fakes)
    message, _ = _failed(m.places, store, story_id, step="places", llm=FakeLLM(), fakes=fakes)
    assert message == "Propose or list the places first."

    _run(m.places_proposal, store, story_id, step="places_proposal", llm=FakeLLM(P0=[P0_REPLY]), fakes=None)
    llm = FakeLLM(P1=[P1_BEACH, P1_FIRE], R1=[dict(R1_PHONE, owner="Mangella")])
    summary, _ = _run(m.places, store, story_id, step="places", llm=llm, fakes=fakes)

    assert len(summary["created"]) == 3
    # The proposal named Kiwilo as the owner: kept over R1's.
    assert store.read_entity(story_id, "props", "prop_le_coco_telephone")["owner_char_id"] == "char_kiwilo"


# ===================================================================== season

S1_REPLY = {"arc": [
    {"ep": 1, "function": "setup", "summary": "Les couples arrivent sur l'île et le premier vote tombe."},
    {"ep": 2, "function": "midpoint_twist", "summary": "Le coco-téléphone révèle une trahison."},
    {"ep": 3, "function": "climax_and_reset", "summary": "Le dernier couple affronte la vérité."},
]}


def s2(summary, characters=("Kiwilo", "Mangella")):
    return {"summary": summary, "open_hooks_in": [], "open_hooks_out": ["Qui a volé le coco-téléphone ?"],
            "characters": list(characters)}


def _season_story(store):
    story_id = _with_cast(store)
    _approve_cast(store, story_id)
    return story_id


def test_the_season_needs_the_cast_approved_and_3_to_12_episodes(store):
    m = _new()
    story_id = _with_cast(store)
    message, _ = _failed(m.season, store, story_id, step="season", llm=FakeLLM(), fakes=None)
    assert message == "Approve the cast first."
    _approve_cast(store, story_id)
    for bad in (2, 13, "8", True, 8.0):
        message, _ = _failed(m.season, store, story_id, step="season", llm=FakeLLM(), fakes=None,
                             params={"episodes": bad})
        assert message == f"A season has 3 to 12 episodes, not {bad!r}."
    assert store.read_doc(story_id, "season.json") is None


def test_the_season_writes_the_skeleton_then_each_entry(store):
    m = _new()
    story_id = _season_story(store)
    seen = []

    def first_s2(call):
        seen.append(store.read_doc(story_id, "season.json"))
        return s2("Kiwilo et Mangella arrivent en couple ; le premier vote les sépare presque.",
                  ("Kiwilo", "Mangella", "Zorglub"))

    llm = FakeLLM(S1=[S1_REPLY], S2=[first_s2, s2("Le coco-téléphone sonne."), s2("La vérité éclate.")])
    summary, log = _run(m.season, store, story_id, step="season", llm=llm, fakes=None, params={"episodes": 3})

    assert summary == {"episodes": 3, "expanded": [1, 2, 3]}
    assert [len(llm.of(p)) for p in ("S1", "S2")] == [1, 3]
    assert "Write the season arc skeleton for 3 episodes." in llm.of("S1")[0]["user"]
    # The skeleton was on disk before the first S2.
    [skeleton] = seen
    assert [entry["summary"] for entry in skeleton["arc"]] == [entry["summary"] for entry in S1_REPLY["arc"]]
    assert skeleton["arc"][0]["open_hooks_out"] == [] and skeleton["arc"][0]["characters"] == []
    season = store.read_doc(story_id, "season.json")
    assert season["episodes_planned"] == 3
    assert season["series_memory"] == {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}}
    assert season["arc"][0]["characters"] == ["char_kiwilo", "char_mangella"]
    assert season["arc"][0]["open_hooks_out"] == ["Qui a volé le coco-téléphone ?"]
    assert season["arc"][2]["summary"] == "La vérité éclate."
    assert "ℹ️ Episode 1: 'Zorglub' is no character of the story; left out." in log
    assert "- ep1 (setup): Les couples arrivent" in llm.of("S2")[0]["user"]


def test_a_failing_entry_keeps_its_outline_the_rest_are_written_and_the_arc_is_unapproved(store):
    m = _new()
    story_id = _season_story(store)
    store.update(story_id, lambda doc: doc["approvals"].__setitem__("season", NOW), now=NOW)
    bad = s2("x " * 61)
    llm = FakeLLM(S1=[S1_REPLY], S2=[s2("Un."), bad, bad, s2("Trois.")])

    message, _log = _failed(m.season, store, story_id, step="season", llm=llm, fakes=None,
                            params={"episodes": 3})

    assert message.startswith("Season arc incomplete: episode 2 failed (the reply failed validation twice: ")
    assert message.endswith("Each keeps its outline; regenerate 'season:2' to finish it.")
    season = store.read_doc(story_id, "season.json")
    assert [entry["summary"] for entry in season["arc"]] == ["Un.", S1_REPLY["arc"][1]["summary"], "Trois."]
    assert store.get(story_id)["approvals"]["season"] is None


# ================================================================= regenerate

def _full(store, **story_kwargs):
    """A story with a cast of three drawn with an editor, two places and a prop."""
    m = _new()
    fakes = _fakes()
    story_id = _with_cast(store, fakes, **story_kwargs)
    _run(m.places, store, story_id, step="places", llm=FakeLLM(P1=[P1_BEACH, P1_FIRE], R1=[R1_PHONE]),
         fakes=fakes, params=PLACES_PARAMS)
    return story_id, fakes


def _regenerate(store, story_id, fakes, params, *, llm=None, settings=None):
    m = _new()
    ctx, log = _ctx(store, story_id, step="regenerate", params=params, settings=settings)
    llm = llm or FakeLLM()
    summary = m.regenerate.run(ctx, runner=llm, time_fn=lambda: 100.0, sleep_fn=_no_sleep,
                               adapters=fakes.adapters)
    return summary, log, llm


def _refused(store, story_id, fakes, params, *, llm=None, settings=None):
    with pytest.raises(steps.StepFailed) as caught:
        _regenerate(store, story_id, fakes, params, llm=llm, settings=settings)
    return str(caught.value)


def _approve_all(store, story_id):
    for kind in ("characters", "places", "props"):
        for doc in store.list_entities(story_id, kind):
            doc["approved_at"] = NOW
            store.write_entity(story_id, kind, doc, now=NOW)
    approvals = store.get(story_id)["approvals"]
    assert approvals["cast"] and approvals["places"]


def _others_unchanged(before, after, key):
    return {k: v for k, v in before.items() if not k.startswith(key)} == {
        k: v for k, v in after.items() if not k.startswith(key)}


def test_regenerate_character_text_rewrites_only_that_characters_text(store):
    story_id, fakes = _full(store)
    _approve_all(store, story_id)
    before = _entity_bytes(store, story_id, "characters")
    kiwi_before = store.read_entity(story_id, "characters", "char_kiwilo")
    older = k1("an older anthropomorphic kiwi with greying fuzz and a cane", ["thin gold chain", "cane"],
               sample="De mon temps, on votait autrement.", relationships=[("Mangella", "son ancienne rivale")])

    summary, log, llm = _regenerate(store, story_id, fakes,
                                    {"target": "character:char_kiwilo:text", "note": "plus vieux"},
                                    llm=FakeLLM(K1=[older]))

    [call] = llm.of("K1")
    assert "Current values:\n- descriptor: " + KIWI in call["user"]
    assert "Rewrite only `text`, following the author's note: plus vieux" in call["user"]
    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    assert kiwi["descriptor"] == older["descriptor"]
    assert kiwi["relationships"] == {"char_mangella": "son ancienne rivale"}
    assert kiwi["voice_hints"]["sample_line"] == "De mon temps, on votait autrement."
    assert kiwi["refs"] == kiwi_before["refs"] and kiwi["voice"] == kiwi_before["voice"]
    assert kiwi["approved_at"] is None
    assert _others_unchanged(before, _entity_bytes(store, story_id, "characters"), "char_kiwilo/character.json")
    approvals = store.get(story_id)["approvals"]
    assert approvals["cast"] is None and approvals["places"]
    assert summary == {"target": "character:char_kiwilo:text"}
    assert "🔁 Regenerated character:char_kiwilo:text (note: plus vieux)" in log


def test_regenerate_a_portrait_uses_a_fresh_seed_and_remakes_the_existing_sheets(store):
    story_id, fakes = _full(store)
    _approve_all(store, story_id)
    kiwi_before = store.read_entity(story_id, "characters", "char_kiwilo")
    mango_bytes = _entity_bytes(store, story_id, "characters")
    t2i_before, edits_before = len(fakes.t2i.requests), len(fakes.editor.requests)

    summary, _log, _llm = _regenerate(store, story_id, fakes, {"target": "character:char_kiwilo:image:portrait",
                                                                "note": "Kiwilo sourit, près du camp"})

    [portrait] = fakes.t2i.requests[t2i_before:]
    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    assert portrait.seed == kiwi["ref_seed"] == kiwi["refs"]["portrait"]["seed"]
    assert portrait.seed != kiwi_before["ref_seed"]
    assert portrait.prompt.endswith("Author's note: the character sourit, près du camp.")
    # Both sheets drawn again from the new portrait, with its seed.
    sheets = fakes.editor.requests[edits_before:]
    assert [r.extra["name"] for r in sheets] == ["turnaround", "expressions"]
    assert {r.seed for r in sheets} == {portrait.seed}
    assert kiwi["refs"]["turnaround"]["created_at"] != kiwi_before["refs"]["turnaround"]["created_at"]
    assert summary["images"] == {"portrait": "base", "turnaround": "references", "expressions": "references"}
    assert kiwi["approved_at"] is None and store.get(story_id)["approvals"]["cast"] is None
    after = _entity_bytes(store, story_id, "characters")
    assert {k: v for k, v in after.items() if k.startswith("char_mangella/")} == {
        k: v for k, v in mango_bytes.items() if k.startswith("char_mangella/")}


def test_regenerate_one_sheet_touches_that_sheet_only_with_a_fresh_seed(store, monkeypatch):
    m = _new()
    story_id, fakes = _full(store)
    kiwi_before = store.read_entity(story_id, "characters", "char_kiwilo")
    monkeypatch.setattr(m.entities, "fresh_seed", lambda: 424242)
    edits_before = len(fakes.editor.requests)

    summary, _log, _llm = _regenerate(store, story_id, fakes, {"target": "character:char_kiwilo:image:expressions"})

    [request] = fakes.editor.requests[edits_before:]
    assert (request.extra["name"], request.seed) == ("expressions", 424242)
    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    assert kiwi["refs"]["expressions"]["seed"] == 424242
    for key in ("portrait", "turnaround", "uploads", "extra"):
        assert kiwi["refs"][key] == kiwi_before["refs"][key]
    assert kiwi["ref_seed"] == kiwi_before["ref_seed"]
    assert summary["images"] == {"expressions": "references"}


def test_a_portrait_regenerate_with_no_editor_keeps_the_old_sheets_and_says_so(store):
    story_id, fakes = _full(store)
    kiwi_before = store.read_entity(story_id, "characters", "char_kiwilo")

    summary, log, _llm = _regenerate(store, story_id, fakes, {"target": "character:char_kiwilo:image:portrait"},
                                     settings=NO_EDITOR)

    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    assert kiwi["refs"]["portrait"] != kiwi_before["refs"]["portrait"]
    assert (kiwi["refs"]["turnaround"], kiwi["refs"]["expressions"]) == (
        kiwi_before["refs"]["turnaround"], kiwi_before["refs"]["expressions"])
    assert [item["char_id"] for item in summary["needs_editor"]] == ["char_kiwilo"]
    assert fakes.paid_editor.calls == 0
    assert any(line.startswith("🟡 Kiwilo: the turnaround and expressions still show the previous portrait")
               for line in log)


def test_regenerate_a_voice_refuses_another_leads_voice_and_otherwise_pins_and_samples(store):
    story_id, fakes = _full(store)
    _approve_all(store, story_id)
    tts_before = len(fakes.tts.requests)

    message = _refused(store, story_id, fakes, {"target": "character:char_kiwilo:voice",
                                                "voice": {"provider": "edge", "voice_id": "fr-FR-DeniseNeural"}})
    assert message == ("edge/fr-FR-DeniseNeural is already Mangella's voice: no two leads or supports share a "
                       "voice; pick another for Kiwilo.")
    message = _refused(store, story_id, fakes, {"target": "character:char_kiwilo:voice",
                                                "voice": {"provider": "edge", "voice_id": "en-US-GuyNeural"}})
    assert message.startswith("edge/en-US-GuyNeural is not a fr voice TTS_CHAIN can reach; pick one of: ")
    assert len(fakes.tts.requests) == tts_before
    assert store.read_entity(story_id, "characters", "char_kiwilo")["approved_at"] == NOW

    summary, _log, _ = _regenerate(store, story_id, fakes, {
        "target": "character:char_kiwilo:voice",
        "voice": {"provider": "edge", "voice_id": "fr-CA-ThierryNeural", "rate": "+5%", "pitch": "-2Hz"}})
    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    assert (kiwi["voice"]["voice_id"], kiwi["voice"]["rate"], kiwi["voice"]["pitch"]) == (
        "fr-CA-ThierryNeural", "+5%", "-2Hz")
    assert fakes.tts.requests[tts_before:] == [("edge/fr-CA-ThierryNeural", "Je gagne toujours, mon cœur.")]
    assert summary == {"target": "character:char_kiwilo:voice", "voice": "edge/fr-CA-ThierryNeural",
                       "sample": "voice_sample.mp3"}
    assert kiwi["approved_at"] is None and store.get(story_id)["approvals"]["cast"] is None

    # No voice given: the best other one -- never the current one, never another lead's; rate and pitch kept.
    summary, _log, _ = _regenerate(store, story_id, fakes, {"target": "character:char_kiwilo:voice"})
    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    assert kiwi["voice"]["voice_id"] == "fr-FR-HenriNeural"
    assert (kiwi["voice"]["rate"], kiwi["voice"]["pitch"]) == ("+5%", "-2Hz")


def test_regenerate_a_voice_can_pin_the_characters_own_recording_through_chatterbox(store, monkeypatch):
    """Plan 23 stage B4: ``chatterbox/reference`` is no catalogue voice and is never "taken", but it needs the
    character's recording (its entry and its file) and chatterbox; the sample is then spoken by the local link."""
    from clipping.providers import tts

    story_id, fakes = _full(store)
    _approve_all(store, story_id)
    local = SimpleNamespace(adapters={**fakes.adapters, ("tts", "local"): fakes.tts})
    pin = {"target": "character:char_kiwilo:voice", "voice": {"provider": "chatterbox", "voice_id": "reference"}}

    monkeypatch.setattr(tts, "_installed", lambda name: True)
    assert "has no voice reference" in _refused(store, story_id, local, pin)

    source = Path(store.story_dir(story_id)) / "incoming.wav"
    source.write_bytes(b"RIFF-recording")
    store.write_media(story_id, "characters", "char_kiwilo", "voice_reference.wav", str(source))
    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    kiwi["voice_reference"] = {"name": "voice_reference.wav", "sha256": "a" * 64, "duration_s": 8.0,
                               "uploaded_at": NOW, "consent": True}
    store.write_entity(story_id, "characters", kiwi, now=NOW)

    monkeypatch.setattr(tts, "_installed", lambda name: False)
    assert "chatterbox is not installed" in _refused(store, story_id, local, pin)

    monkeypatch.setattr(tts, "_installed", lambda name: True)
    tts_before = len(fakes.tts.requests)
    summary, _log, _ = _regenerate(store, story_id, local, pin)

    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    assert (kiwi["voice"]["provider"], kiwi["voice"]["voice_id"]) == ("chatterbox", "reference")
    assert fakes.tts.requests[tts_before:] == [("local/chatterbox", "Je gagne toujours, mon cœur.")]
    assert summary["voice"] == "chatterbox/reference" and kiwi["approved_at"] is None


def test_regenerate_places_and_props_touch_only_their_item(store, monkeypatch):
    m = _new()
    story_id, fakes = _full(store, mode="prompt_only")
    _approve_all(store, story_id)
    places_before = _entity_bytes(store, story_id, "places")
    monkeypatch.setattr(m.entities, "fresh_seed", lambda: 99)

    darker = dict(P1_BEACH, descriptor="a crescent of grey sand under storm clouds", time_variants=["day", "rain"])
    _regenerate(store, story_id, fakes, {"target": "place:place_le_camp_de_plage:text", "note": "plus sombre"},
                llm=FakeLLM(P1=[darker]))
    beach = store.read_entity(story_id, "places", "place_le_camp_de_plage")
    assert beach["descriptor"] == darker["descriptor"]
    # The plate stays; the proposed variants follow P1; an unmade variant it dropped goes.
    assert list(beach["time_variants"]) == ["day", "rain"] and beach["time_variants"]["day"] is not None
    assert beach["approved_at"] is None and store.get(story_id)["approvals"]["places"] is None
    assert _others_unchanged(places_before, _entity_bytes(store, story_id, "places"), "place_le_camp_de_plage/")

    t2i_before = len(fakes.t2i.requests)
    summary, _log, _ = _regenerate(store, story_id, fakes, {"target": "place:place_le_feu_d_elimination:image:dusk"})
    [request] = fakes.t2i.requests[t2i_before:]
    assert request.seed == 99
    fire = store.read_entity(story_id, "places", "place_le_feu_d_elimination")
    assert list(fire["time_variants"]) == ["day", "dusk"]
    assert fire["time_variants"]["dusk"]["consistency"] == "prompt_only"
    assert summary["images"] == {"dusk": "prompt_only"}

    props_before = _entity_bytes(store, story_id, "props")
    _regenerate(store, story_id, fakes, {"target": "prop:prop_le_coco_telephone:text"},
                llm=FakeLLM(R1=[{"descriptor": "a cracked coconut phone", "owner": None}]))
    phone = store.read_entity(story_id, "props", "prop_le_coco_telephone")
    assert (phone["descriptor"], phone["owner_char_id"]) == ("a cracked coconut phone", None)
    assert phone["image"] is not None

    _regenerate(store, story_id, fakes, {"target": "prop:prop_le_coco_telephone:image",
                                         "note": "comme au Le camp de plage, tenu par Mangella"})
    assert fakes.t2i.requests[-1].prompt.endswith(
        "Author's note: comme au the place, tenu par the character.")
    assert fakes.t2i.requests[-1].seed == 99
    assert set(_entity_bytes(store, story_id, "props")) == set(props_before)


def test_regenerate_a_season_entry(store):
    m = _new()
    story_id = _season_story(store)
    _run(m.season, store, story_id, step="season", llm=FakeLLM(S1=[S1_REPLY], S2=[s2("Un."), s2("Deux."),
                                                                                  s2("Trois.")]),
         fakes=None, params={"episodes": 3})
    store.update(story_id, lambda doc: doc["approvals"].__setitem__("season", NOW), now=NOW)
    before = store.read_doc(story_id, "season.json")

    llm = FakeLLM(S2=[s2("Deux, en plus tendu.", ("Mangella",))])
    summary, _log, _ = _regenerate(store, story_id, _fakes(), {"target": "season:2", "note": "plus tendu"}, llm=llm)

    [call] = llm.of("S2")
    assert "Current values:\n- summary: Deux." in call["user"]
    assert "- ep2 (midpoint_twist): Deux.  <- expand this one" in call["user"]
    season = store.read_doc(story_id, "season.json")
    assert season["arc"][1]["summary"] == "Deux, en plus tendu."
    assert season["arc"][1]["characters"] == ["char_mangella"]
    assert season["arc"][0] == before["arc"][0] and season["arc"][2] == before["arc"][2]
    assert store.get(story_id)["approvals"]["season"] is None
    assert summary == {"target": "season:2", "summary": "Deux, en plus tendu."}


def test_regenerate_refuses_unknown_ids_shapes_and_extra_images(store):
    story_id, fakes = _full(store)
    llm = FakeLLM()
    counts = fakes.counts()

    assert _refused(store, story_id, fakes, {"target": "character:char_zorglub:text"}, llm=llm) == (
        "Cannot regenerate 'character:char_zorglub:text': there is no character 'char_zorglub' in this story.")
    assert _refused(store, story_id, fakes, {"target": "prop:prop_nope:image"}, llm=llm) == (
        "Cannot regenerate 'prop:prop_nope:image': there is no prop 'prop_nope' in this story.")
    assert _refused(store, story_id, fakes, {"target": "season:4"}, llm=llm) == (
        "Cannot regenerate 'season:4': there is no season arc yet; write the season first.")
    assert _refused(store, story_id, fakes, {"target": "character:char_kiwilo:image:extra:1"}, llm=llm) == (
        "Cannot regenerate 'character:char_kiwilo:image:extra:1': extra images arrive in a later phase.")
    # Phase 3 (stage 6) runs the episode targets: this story is not ready, so it is refused as such.
    assert _refused(store, story_id, fakes, {"target": "scene:1:s02"}, llm=llm) == (
        "Cannot regenerate 'scene:1:s02': The story is not ready yet: approve the cast, the places and the "
        "season first.")
    for target in ("character:char_kiwilo:image:selfie", "place:place_le_camp_de_plage:image:golden_hour",
                   "prop:prop_le_coco_telephone:voice", "character:Kiwilo:text", "season:0", "season:x"):
        message = _refused(store, story_id, fakes, {"target": target}, llm=llm)
        assert message.startswith(f"Cannot regenerate {target!r}: the valid targets are bible:logline, ")
        for shape in ("character:<char_id>:text", "character:<char_id>:image:portrait|turnaround|expressions",
                      "place:<place_id>:image:day|night|dusk|rain|dawn", "prop:<prop_id>:image", "season:<ep>"):
            assert shape in message
    assert llm.calls == [] and fakes.counts() == counts


# ========================================================== refimages seeds

def test_an_image_seed_override_is_the_request_seed_and_a_portraits_becomes_ref_seed(store):
    m = _new()
    story_id, fakes = _full(store)
    kwargs = {"env": SETTINGS, "on_log": Log(), "cancel": CancelToken(), "adapters": fakes.adapters,
              "sleep_fn": _no_sleep, "time_fn": lambda: 100.0}

    ref = m.refimages.character_image(store, story_id, "char_mangella", "portrait", seed=5, **kwargs)
    assert fakes.t2i.requests[-1].seed == 5 == ref["seed"]
    assert store.read_entity(story_id, "characters", "char_mangella")["ref_seed"] == 5
    m.refimages.prop_image(store, story_id, "prop_le_coco_telephone", seed=0, **kwargs)
    assert fakes.t2i.requests[-1].seed == 0
    for bad in (-1, True, 1.5, "7"):
        with pytest.raises(m.refimages.RefImageError, match="A seed must be a whole number"):
            m.refimages.place_image(store, story_id, "place_le_camp_de_plage", "day", seed=bad, **kwargs)


# ================================================================== registry

def test_the_phase_2_steps_are_registered_and_the_registry_stays_light():
    for name in ("cast", "places_proposal", "places", "season"):
        assert callable(steps.RUNNERS[name]), name
    code = (
        "import sys, clipping.aistory.steps\n"
        "heavy = ('clipping.providers.llm', 'clipping.aistory.prompts', 'clipping.config',\n"
        "         'clipping.aistory.refimages', 'clipping.aistory.voices', 'clipping.aistory.uploads',\n"
        "         'clipping.aistory.steps.cast', 'clipping.aistory.steps.places',\n"
        "         'clipping.aistory.steps.season', 'clipping.aistory.steps.entities')\n"
        "print(sorted(name for name in heavy if name in sys.modules))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


def test_the_registry_runs_the_cast_step(store):
    story_id = _story(store, approve=("concept", "bible"))
    ctx, _ = _ctx(store, story_id, step="cast", params=CAST_PARAMS)
    with pytest.raises(Exception) as caught:
        steps.run("cast", ctx)
    assert type(caught.value).__name__ == "StepFailed", repr(caught.value)
    assert str(caught.value) == "Approve the style first."
    assert json.loads(json.dumps(steps.RUNNERS["season"].__name__)) == "run_season"
