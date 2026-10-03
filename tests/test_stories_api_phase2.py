"""The AI Story API, steps 5-7 (phase 2, stage 7; spec 3, 9.1, 9.2; phase-2 plan 2
"API").

``web/api/routes/stories.py`` on a throwaway app -- the stories router and the
jobs router, never the singleton ``web.api.app.app`` -- with the job store on
an empty table persisted under ``tmp_path``, ``worker.OUTPUTS_ROOT`` under
``tmp_path`` and ``worker.submit_job`` replaced by a recorder. A step that has
to *run* goes through the real worker path (``worker._execute_story_step``)
with the registered runner answered by stand-ins: the LLM per prompt id, the
image chains, Edge TTS and the vision chain through fake adapters. Hermetic
like ``tests/test_story_cast_steps.py``: no key, chain, cap or limit of the
machine reaches a test (``clipping.config`` loads the main checkout's
``.env``, A-049: every provider key is cleared), no request leaves the
process, and the repository's ``data/`` files and ``outputs/stories`` are
fingerprinted before and after every test.

The text guards and the step- and rule-level tests at the top run in the
pytest-only CI environment (DEC-012); everything that needs the app skips
there, like the other route tests. Every new module and name is reached
inside the tests, so on the parent commit each test fails on its own rather
than the file failing to collect.
"""

from __future__ import annotations

import ast
import asyncio
import copy
import hashlib
import importlib
import io
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping.aistory import prompting, prompts, schemas, steps, stylelock, templates
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from clipping.providers.generation import GenResult
from clipping.providers.registry import Link
from clipping.providers.transport import APIConnectionError, Response

ROOT = Path(__file__).resolve().parents[1]
MODELS = ROOT / "web" / "api" / "models.py"
NOW = "2026-09-26T10:00:00+00:00"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
MIB = 1024 * 1024
LINK = Link("gemini", "gemini-test")
UNKNOWN_ID = "0123456789ab"

GEN_VARS = (
    "FAL_KEY", "OPENAI_API_KEY", "GOOGLE_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID",
    "POLLINATIONS_API_KEY", "OPENROUTER_API_KEY", "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN",
    "TTS_CHAIN", "VISION_CHAIN", "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL",
    "ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE",
    "LLM_CHAIN", "ALLOW_SLOW_CHAIN", "MAX_QUEUED_JOBS",
)
REAL_FILES = tuple(ROOT / "data" / name for name in ("usage.json", "spend.json", "chain_test_ledger.json"))
REAL_STORIES = (ROOT / "outputs" / "stories", ROOT / "outputs" / "stories.json")

# Test values only: every request goes to a fake.
BASE = {
    "LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key",
    "IMAGE_CHAIN": "pollinations/flux", "TTS_CHAIN": "edge/fr-FR-HenriNeural",
    "VISION_CHAIN": "gemini/flash-lite",
}
# The free route of this machine: a free text-to-image link, and no editor --
# fal has no key, and would be paid anyway.
NO_EDITOR = dict(BASE, IMAGE_EDIT_CHAIN="fal/seedream-4-edit")
# A local editor that answers (a fake): the sheets are edits.
EDITOR = dict(BASE, IMAGE_EDIT_CHAIN="local/comfyui")
PAID_EDITOR = dict(BASE, IMAGE_EDIT_CHAIN="fal/seedream-4-edit", FAL_KEY="test-fal-key")
SEEDREAM_PER_EDIT = 0.03

TENTAFRUIT = next(c for c in templates.load_concepts() if c["concept_id"] == "tentafruit_island")

KIWI = "an anthropomorphic kiwi with fuzzy brown skin and bright green flesh at the mouth"
MANGO = "an anthropomorphic mango with smooth orange-yellow skin and a sly grin"
FIG = "an anthropomorphic fig with soft purple skin and a tiny green stem"
GOOD_NOTES = "Round green kiwi body, fuzzy brown skin, tiny red scarf, big white sneakers"


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


# ------------------------------------------------------------------- fakes

class Log(list):
    def __call__(self, line):
        self.append(str(line))


class FakeLLM:
    """Stands in for ``llm.run_chain``: answers each prompt id from its own
    queue, records every call. An entry is a reply, an exception to raise,
    or ``f(call)`` returning either."""

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
    """An image adapter: records every probe and request, writes a small PNG
    whose bytes differ from one call to the next."""

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


class FakeVision:
    """A vision adapter answering U1 with *notes* (the free Gemini link)."""

    def __init__(self, notes=GOOD_NOTES):
        self.notes = notes
        self.requests = []

    def estimate(self, link, request):
        return None

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(copy.copy(request))
        return GenResult(provider=link.provider, model=link.model, paths=(),
                         meta={"text": json.dumps({"appearance_notes": self.notes})})


class ComfyStatus:
    """ComfyUI's status route as the local adapter's transport: *up* answers
    ``GET /system_stats``, down refuses the connection. Records every URL and
    timeout; any other request fails the test."""

    def __init__(self, *, up):
        self.up = up
        self.urls = []
        self.timeouts = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=None):
        self.urls.append(url)
        self.timeouts.append(timeout)
        if method != "GET" or not url.endswith("/system_stats"):
            raise AssertionError(f"not a status probe: {method} {url}")
        if not self.up:
            raise APIConnectionError(f"{method} {url}: [Errno 111] Connection refused")
        stats = {"system": {"comfyui_version": "0.3.9"}, "devices": [{"name": "cuda:0 test"}]}
        return Response(200, {}, json.dumps(stats).encode())


class Fakes(SimpleNamespace):
    @property
    def adapters(self):
        return {("image", "pollinations"): self.t2i, ("image_edit", "local"): self.editor,
                ("image_edit", "fal"): self.paid_editor, ("tts", "edge"): self.tts,
                ("vision", "gemini"): self.vision}


def _fakes():
    return Fakes(t2i=FakeImage(), editor=FakeImage(), paid_editor=FakeImage(), tts=FakeTTS(),
                 vision=FakeVision())


def _no_sleep(seconds):
    raise AssertionError(f"the chain tried to sleep {seconds}s")


# ----------------------------------------------------------------- replies

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
            sample="Oh non, pas encore un vote !")

CAST_PARAMS = {"selected": ["Kiwilo", "Mangella"],
               "custom": [{"name": "Figuette", "role": "support", "one_line": "Une figue timide qui voit tout."}]}
IDS = ["char_kiwilo", "char_mangella", "char_figuette"]

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
BEACH, FIRE, PHONE = "place_le_camp_de_plage", "place_le_feu_d_elimination", "prop_le_coco_telephone"

S1_REPLY = {"arc": [
    {"ep": 1, "function": "setup", "summary": "Les couples arrivent sur l'île et le premier vote tombe."},
    {"ep": 2, "function": "midpoint_twist", "summary": "Le coco-téléphone révèle une trahison."},
    {"ep": 3, "function": "climax_and_reset", "summary": "Le dernier couple affronte la vérité."},
]}


def s2(summary, characters=("Kiwilo", "Mangella")):
    return {"summary": summary, "open_hooks_in": [], "open_hooks_out": ["Qui a volé le coco-téléphone ?"],
            "characters": list(characters)}


BIBLE = {
    "logline": "Des fruits en couple survivent au vote hebdomadaire d'une île de téléréalité.",
    "premise": "Chaque semaine, un couple est éliminé. Le coco-téléphone annonce le vote.",
    "tone": "mélodramatique, rapide",
}


# ------------------------------------------------------------ the story

def _lock():
    draft = stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=NOW)
    return stylelock.lock_style(draft, now=NOW)


def _story(store, *, mode="references", route="auto", approve=("concept", "bible", "style")):
    """A French Tentafruit story: concept chosen, bible written, style
    locked, and the approvals in *approve* given (status ``style_approved``
    by default)."""
    story_id = store.create(language="fr", style_template_id="fruit_drama", now=NOW,
                            generation_profile={"route": route, "consistency_mode": mode})["story_id"]
    concept = templates.localize_concept(TENTAFRUIT, "fr")

    def setup(doc):
        doc["concept_id"] = TENTAFRUIT["concept_id"]
        doc["concept"] = concept
        doc["title"] = concept["title"]
        doc.update(BIBLE)
        for key in approve:
            doc["approvals"][key] = NOW

    store.update(story_id, setup, now=NOW)
    store.write_doc(story_id, "style_lock.json", _lock(), now=NOW, validator=schemas.style_lock_errors)
    return story_id


def _new_character(store, story_id, char_id, name, *, role="lead", uploads=()):
    from clipping.aistory.steps import cast

    doc = cast.new_character(char_id, name, role, f"{name} veut gagner.", archetype=None, source="custom",
                             now=NOW)
    doc["refs"]["uploads"] = [dict(entry) for entry in uploads]
    return store.write_entity(story_id, "characters", doc, now=NOW)


def _plant_upload(store, story_id, char_id, name, tmp_path, *, description=None):
    """A design reference on disk and in the character's list (no decoding:
    the vision adapter is a fake)."""
    src = tmp_path / f"src-{name}"
    src.write_bytes(PNG)
    store.write_media(story_id, "characters", char_id, name, str(src))
    doc = store.read_entity(story_id, "characters", char_id)
    doc["refs"]["uploads"].append({"name": name, "description": description, "uploaded_at": NOW})
    store.write_entity(story_id, "characters", doc, now=NOW)


def _upload_name(n):
    return f"{n:032x}.png"


# ================================================== text guards and rules (CI)

def _class_fields(name: str) -> list:
    """A pydantic model's field names, in order, read without importing pydantic."""
    tree = ast.parse(MODELS.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return [stmt.target.id for stmt in node.body
                    if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)]
    raise AssertionError(f"{name} not found in models.py")


def _class_source(name: str) -> str:
    text = MODELS.read_text(encoding="utf-8")
    body = text[text.index(f"class {name}("):]
    return body[: body.index("\nclass ")] if "\nclass " in body else body


def test_the_patch_models_declare_exactly_the_editable_fields():
    from clipping.aistory import workflow

    assert _class_fields("CharacterPatchRequest") == list(workflow.CHARACTER_PATCH_FIELDS)
    assert _class_fields("PlacePatchRequest") == list(workflow.PLACE_PATCH_FIELDS)
    assert _class_fields("PropPatchRequest") == list(workflow.PROP_PATCH_FIELDS)
    assert "voice" in _class_fields("StoryRegenerateRequest")
    # Default-strict like StoryPatchRequest: no extra= override, "sent" is model_fields_set.
    for name in ("CharacterPatchRequest", "PlacePatchRequest", "PropPatchRequest", "StoryRegenerateRequest"):
        assert "extra" not in _class_source(name), name


def test_the_phase_2_grammar_moved_out_of_the_later_phases():
    from clipping.aistory import workflow

    # Phase 7 stage 5b (DEC-228): re-pinned on purpose -- the knowledge step, after the season.
    assert workflow.PHASE2_STEPS == ("cast", "places_proposal", "places", "season", "knowledge")
    assert not set(workflow.PHASE2_STEPS) & set(workflow.LATER_STEPS)
    assert workflow.PHASE1_STEPS == ("concepts", "bible", "style", "style_preview")  # the CLI's choices
    for target in ("character:char_kiwilo:text", "character:char_kiwilo:image:portrait",
                   "character:char_kiwilo:image:expressions", "character:char_kiwilo:voice",
                   "place:place_beach:text", "place:place_beach:image:night", "prop:prop_phone:text",
                   "prop:prop_phone:image", "season:12"):
        assert workflow.check_regenerate_target(target) is None, target
    for target in ("character:char_kiwilo:image:extra:1", "shot:1:sh03:frames"):
        with pytest.raises(workflow.WorkflowError) as caught:
            workflow.check_regenerate_target(target)
        assert caught.value.code == "later_phase"
    for target in ("character:char_kiwilo:nope", "character:BAD:text", "season:0", "place:place_x:image:sunset",
                   "prop:prop_phone:image:day", "character:", "nope"):
        with pytest.raises(workflow.WorkflowError) as caught:
            workflow.check_regenerate_target(target)
        assert caught.value.code == "invalid", target
        assert "character:<char_id>:text" in caught.value.detail and "bible:logline" in caught.value.detail
    # The approvals phase 2 takes are no longer "a later phase".
    for doc in ("character:char_kiwilo", "place:place_beach", "prop:prop_phone", "season"):
        with pytest.raises(workflow.WorkflowError) as caught:
            workflow.refuse_approval(doc)
        assert caught.value.code == "not_found"


def test_what_a_character_place_and_prop_lack_and_the_progress_they_add_up_to(tmp_path):
    from clipping.aistory import workflow

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store)
    _new_character(store, story_id, "char_kiwilo", "Kiwilo")
    story = store.get(story_id)

    progress = workflow.progress(store, story, env=NO_EDITOR)
    assert progress == {
        "characters": {"char_kiwilo": {"missing": ["text", "portrait", "turnaround", "expressions", "voice",
                                                   "sample"], "needs_editor": False}},
        "places": {}, "props": {}, "pick_voice": [], "edit_readiness": progress["edit_readiness"],
    }
    # Two sheets missing: the editor's verdict, calling nothing (fal has no key).
    assert progress["edit_readiness"]["ready"] is False
    assert progress["edit_readiness"]["units"] == {"images": 2}
    assert "FAL_KEY" in progress["edit_readiness"]["message"]

    # Written by K1 but left without a voice (none was free): "pick a voice".
    doc = store.read_entity(story_id, "characters", "char_kiwilo")
    doc.update(descriptor=KIWI, signature_items=["thin gold chain", "white linen shirt"])
    doc["voice_hints"] = {"gender": "male", "age": "adult", "style_tags": [], "direction": "warm",
                          "sample_line": "Je gagne."}
    store.write_entity(story_id, "characters", doc, now=NOW)
    progress = workflow.progress(store, store.get(story_id), env=NO_EDITOR)
    assert progress["pick_voice"] == ["char_kiwilo"]
    assert progress["characters"]["char_kiwilo"]["missing"] == ["portrait", "turnaround", "expressions", "voice",
                                                                 "sample"]
    # No portrait yet: the character does not wait for an editor, it waits for its portrait.
    assert progress["characters"]["char_kiwilo"]["needs_editor"] is False


def test_progress_with_probe_local_asks_the_local_editor_and_waits_for_one_when_it_is_down(tmp_path, monkeypatch):
    """The story page's question (``probe_local=True``, found in the browser
    review): ComfyUI is the only editor and it does not answer, so a
    character with its portrait waits for an editor -- with the probe's
    reason -- before Continue is pressed. Without it nothing is asked."""
    from clipping.aistory import workflow
    from clipping.providers import local_comfyui

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store)
    _new_character(store, story_id, "char_kiwilo", "Kiwilo")
    src = tmp_path / "portrait.png"
    src.write_bytes(PNG)
    store.write_media(story_id, "characters", "char_kiwilo", "portrait.png", str(src))
    doc = store.read_entity(story_id, "characters", "char_kiwilo")
    doc["refs"]["portrait"] = {"name": "portrait.png", "consistency": "base", "source": "pollinations/flux",
                               "seed": 7, "created_at": NOW}
    store.write_entity(story_id, "characters", doc, now=NOW)
    down = ComfyStatus(up=False)
    monkeypatch.setattr(local_comfyui, "urllib_transport", down)
    env = dict(EDITOR, LOCAL_COMFYUI_URL="http://comfy.test:8188")

    calm = workflow.progress(store, store.get(story_id), env=env)
    assert calm["edit_readiness"]["ready"] is True
    assert calm["characters"]["char_kiwilo"]["needs_editor"] is False
    assert down.urls == []

    asked = workflow.progress(store, store.get(story_id), env=env, probe_local=True)
    assert asked["characters"]["char_kiwilo"]["needs_editor"] is True
    readiness = asked["edit_readiness"]
    assert (readiness["ready"], readiness["route_class"], readiness["units"]) == (False, "blocked", {"images": 2})
    assert readiness["links"][0]["reason"] == (
        "unreachable at http://comfy.test:8188 (GET http://comfy.test:8188/system_stats: [Errno 111] "
        "Connection refused)")
    # One short status request: GET /system_stats, 2 s at most.
    assert down.urls == ["http://comfy.test:8188/system_stats"]
    assert down.timeouts == [local_comfyui.STATUS_PROBE_TIMEOUT_SECONDS] and down.timeouts[0] <= 2.0


def test_cast_units_prices_missing_sheets_as_images_only_in_prompt_only_mode(tmp_path):
    """Workflow-level regression lock for the Tier-2 walk finding
    "prompt-only sheets estimated as edits" (``clipping.aistory.workflow
    .cast_units``): a character with its portrait but no sheets prices the
    missing turnaround/expressions as ``edit_images`` in references mode,
    and as ``images`` (never ``edit_images``) once the story is switched to
    prompt-only -- the same story, re-read after the switch."""
    from clipping.aistory import workflow

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store)  # references mode, style_approved
    _new_character(store, story_id, "char_kiwilo", "Kiwilo")
    src = tmp_path / "portrait.png"
    src.write_bytes(PNG)
    store.write_media(story_id, "characters", "char_kiwilo", "portrait.png", str(src))
    doc = store.read_entity(story_id, "characters", "char_kiwilo")
    doc["refs"]["portrait"] = {"name": "portrait.png", "consistency": "base", "source": "pollinations/flux",
                               "seed": 7, "created_at": NOW}
    store.write_entity(story_id, "characters", doc, now=NOW)

    references_units = workflow.cast_units(store, store.get(story_id))
    assert references_units["edit_images"] == 2 and references_units["images"] == 0

    workflow.patch_story(store, story_id, {"generation_profile": {"consistency_mode": "prompt_only"}}, now=NOW)
    prompt_only_units = workflow.cast_units(store, store.get(story_id))
    assert prompt_only_units["edit_images"] == 0 and prompt_only_units["images"] == 2


def test_target_units_prices_a_missing_place_time_variant_as_images_only_in_prompt_only_mode(tmp_path):
    """Same lock, place side (``workflow.target_units``): a place's time
    variant that is not made yet (made on demand, spec 2.4) prices as
    ``edit_images`` in references mode and as ``images`` once the story is
    prompt-only, matching ``clipping.aistory.steps.places``' own note that
    the master plate is always text-to-image but a variant is drawn from it
    ("edited") unless the story is prompt-only."""
    from clipping.aistory import workflow
    from clipping.aistory.steps import places as places_step
    from clipping.aistory.steps import regenerate as regenerate_step

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store)  # references mode
    place = places_step.new_place("place_beach", "Le camp de plage", "Un feu, des huttes.", now=NOW)
    store.write_entity(story_id, "places", place, now=NOW)
    parsed = regenerate_step.parse_target("place:place_beach:image:night")
    assert parsed == ("place", "place_beach", "image", "night")

    references_units = workflow.target_units(store, store.get(story_id), parsed)
    assert references_units == {"llm_calls": 0, "images": 0, "edit_images": 1, "tts_chars": 0}

    workflow.patch_story(store, story_id, {"generation_profile": {"consistency_mode": "prompt_only"}}, now=NOW)
    prompt_only_units = workflow.target_units(store, store.get(story_id), parsed)
    assert prompt_only_units == {"llm_calls": 0, "images": 1, "edit_images": 0, "tts_chars": 0}


# =================================================== describe before K1 (CI)

def _cast_step(store, story_id, *, params, llm, fakes, settings=NO_EDITOR):
    from clipping.aistory.steps import cast

    log = Log()
    ctx = steps.StepContext(job_id="job000000001", story_id=story_id, step="cast", ep=None, params=params,
                            cancel=CancelToken(), settings_env=dict(settings), outputs_dir=store.outputs_dir,
                            on_log=log)
    return cast.run(ctx, runner=llm, time_fn=lambda: 100.0, sleep_fn=_no_sleep, adapters=fakes.adapters), log


def test_k1_runs_after_every_upload_without_a_description_is_described(tmp_path):
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store)
    _new_character(store, story_id, "char_kiwilo", "Kiwilo")
    _plant_upload(store, story_id, "char_kiwilo", _upload_name(1), tmp_path)
    _plant_upload(store, story_id, "char_kiwilo", _upload_name(2), tmp_path, description="a tiny red scarf")
    fakes = _fakes()
    order = []

    def reply(call):
        order.append("K1")
        return K1_KIWI

    original = fakes.vision.generate

    def generate(*args, **kwargs):
        order.append("U1")
        return original(*args, **kwargs)

    fakes.vision.generate = generate
    llm = FakeLLM(K1=[reply])

    _cast_step(store, story_id, params={}, llm=llm, fakes=fakes)

    # Only the reference with no description was described, and before K1.
    assert order == ["U1", "K1"]
    assert fakes.vision.requests[0].images[0].endswith(_upload_name(1))
    [call] = llm.of("K1")
    assert (f"Design reference supplied by the author: {GOOD_NOTES}; a tiny red scarf; follow it."
            in call["user"])
    uploads = store.read_entity(story_id, "characters", "char_kiwilo")["refs"]["uploads"]
    assert [entry["description"] for entry in uploads] == [GOOD_NOTES, "a tiny red scarf"]


def test_an_upload_that_cannot_be_described_is_recorded_and_k1_proceeds_without_it(tmp_path):
    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store)
    _new_character(store, story_id, "char_kiwilo", "Kiwilo")
    _plant_upload(store, story_id, "char_kiwilo", _upload_name(1), tmp_path)
    fakes = _fakes()
    llm = FakeLLM(K1=[K1_KIWI])
    # The only vision link is paid and allow_paid is off: nothing can describe it.
    settings = dict(NO_EDITOR, VISION_CHAIN="gemini/flash")

    summary, log = _cast_step(store, story_id, params={}, llm=llm, fakes=fakes, settings=settings)

    assert fakes.vision.requests == []
    [call] = llm.of("K1")
    assert "Design reference supplied by the author" not in call["user"]
    kiwi = store.read_entity(story_id, "characters", "char_kiwilo")
    assert kiwi["descriptor"] == KIWI and kiwi["refs"]["uploads"][0]["description"] is None
    assert summary["written"] == ["char_kiwilo"]
    [line] = [line for line in log if "was not described" in line]
    assert line.startswith(f"⚠️ Kiwilo: design reference {_upload_name(1)} was not described -- No vision link")
    assert "allow_paid is off" in line and line.endswith("K1 writes the character without it.")


def test_regenerating_a_characters_text_describes_its_new_upload_first(tmp_path):
    from clipping.aistory.steps import regenerate

    store = StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    story_id = _story(store)
    _new_character(store, story_id, "char_kiwilo", "Kiwilo")
    fakes = _fakes()
    _cast_step(store, story_id, params={}, llm=FakeLLM(K1=[K1_KIWI]), fakes=fakes)
    _plant_upload(store, story_id, "char_kiwilo", _upload_name(7), tmp_path)
    llm = FakeLLM(K1=[K1_KIWI])
    ctx = steps.StepContext(job_id="job000000002", story_id=story_id, step="regenerate", ep=None,
                            params={"target": "character:char_kiwilo:text", "note": None}, cancel=CancelToken(),
                            settings_env=dict(NO_EDITOR), outputs_dir=store.outputs_dir, on_log=Log())

    regenerate.run(ctx, runner=llm, time_fn=lambda: 100.0, sleep_fn=_no_sleep, adapters=fakes.adapters)

    assert len(fakes.vision.requests) == 1
    assert f"Design reference supplied by the author: {GOOD_NOTES}; follow it." in llm.of("K1")[0]["user"]


# ================================================================== the API

@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import jobs, stories

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", dict(NO_EDITOR))
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker, "UPLOADS_ROOT", str(tmp_path / "uploads"))

    submitted = []

    async def fake_submit(job_id, payload):
        submitted.append(job_id)

    monkeypatch.setattr(worker, "submit_job", fake_submit)
    monkeypatch.setenv("DISABLE_AUTH", "1")

    app = FastAPI()
    app.include_router(stories.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, jobs=job_store, worker=worker, outputs=outputs, submitted=submitted, app=app,
            monkeypatch=monkeypatch, tmp_path=tmp_path, routes=stories,
            store=StoryStore(outputs, on_log=lambda line: None), fakes=_fakes(),
        )


def _settings(api, env):
    api.monkeypatch.setattr(api.worker, "_settings_env", dict(env))


def _url(story_id, suffix=""):
    return f"/api/stories/{story_id}{suffix}"


def _post_step(api, story_id, step, params=None):
    return api.client.post(_url(story_id, f"/steps/{step}"), json={"params": params} if params is not None else {})


def _run(api, job_id, llm=None):
    """Run a queued step job the way the worker does: its LLM answered by
    *llm*, its images, voices and vision by the fixture's fakes. Returns the
    job as it ended."""
    job = api.jobs.get_job(job_id)
    step = job["step"]
    module = importlib.import_module(f"clipping.aistory.steps.{step}")
    kwargs = {"runner": llm or FakeLLM(), "time_fn": lambda: 100.0}
    if step not in ("season", "places_proposal"):
        kwargs.update(sleep_fn=_no_sleep, adapters=api.fakes.adapters)
    api.monkeypatch.setitem(steps.RUNNERS, step, lambda ctx: module.run(ctx, **kwargs))
    api.worker._execute_story_step(job_id, job, CancelToken())
    return api.jobs.get_job(job_id)


def _status(api, job_id):
    return api.jobs.get_job(job_id)["status"]


def _job(api, story_id, step, status=None, params=None):
    """A step job straight in the store (for the states the API only reaches
    by running the worker)."""
    from web.api.models import JobStatus

    job_id = api.jobs.create_job(kind="story_step", story_id=story_id, step=step, params=params)
    if status is not None:
        api.jobs.set_status(job_id, JobStatus(status))
    return job_id


def _page(api, story_id):
    response = api.client.get(_url(story_id))
    assert response.status_code == 200, response.text
    return response.json()


def _cast_via_api(api, *, settings=EDITOR, llm=None):
    """A story with the cast of three written, drawn, voiced and sampled
    through the API (sheets as edits when *settings* has an editor)."""
    _settings(api, settings)
    story_id = _story(api.store)
    response = _post_step(api, story_id, "cast", CAST_PARAMS)
    assert response.status_code == 201, response.text
    job = _run(api, response.json()["id"], llm or FakeLLM(K1=[K1_KIWI, K1_MANGO, K1_FIG]))
    assert job["status"] == "awaiting_approval", job.get("error")
    return story_id


def _approve(api, story_id, doc):
    return api.client.post(_url(story_id, f"/approve/{doc}"))


def _full_via_api(api):
    """_cast_via_api, then the proposal and the places and prop, made."""
    story_id = _cast_via_api(api)
    job = _post_step(api, story_id, "places_proposal").json()
    assert _run(api, job["id"], FakeLLM(P0=[P0_REPLY]))["status"] == "awaiting_approval"
    job = _post_step(api, story_id, "places", {}).json()
    assert _run(api, job["id"], FakeLLM(P1=[P1_BEACH, P1_FIRE], R1=[R1_PHONE]))["status"] == "awaiting_approval"
    return story_id


# ======================================================= the whole of steps 5-7

def test_the_phase_2_happy_path_from_style_to_ready(api):
    c = api.client
    fakes = api.fakes
    story_id = _story(api.store)  # style_approved; NO_EDITOR: the free route, no editor

    # Step 5: the cast of three, a job of the story.
    response = _post_step(api, story_id, "cast", CAST_PARAMS)
    assert response.status_code == 201, response.text
    first = response.json()
    assert (first["kind"], first["story_id"], first["step"], first["status"], first["params"]) == (
        "story_step", story_id, "cast", "queued", CAST_PARAMS)
    assert api.submitted == [first["id"]]
    job = _run(api, first["id"], FakeLLM(K1=[K1_KIWI, K1_MANGO, K1_FIG]))
    assert job["status"] == "awaiting_approval", job.get("error")
    # Portraits, voices and samples; no editor was asked for anything.
    assert (len(fakes.t2i.requests), len(fakes.editor.requests), fakes.paid_editor.requests,
            len(fakes.tts.requests)) == (3, 0, [], 3)

    page = _page(api, story_id)
    assert [doc["char_id"] for doc in page["characters"]] == IDS  # leads, then the support
    progress = page["progress"]
    assert progress["characters"] == {cid: {"missing": ["turnaround", "expressions"], "needs_editor": True}
                                      for cid in IDS}
    assert progress["pick_voice"] == [] and progress["places"] == {} and progress["props"] == {}
    assert progress["edit_readiness"]["ready"] is False
    assert progress["edit_readiness"]["units"] == {"images": 6}
    assert (page["season"], page["places_proposal"], page["places"], page["props"]) == (None, None, [], [])
    assert page["story"]["approvals"]["cast"] is None

    # Nothing is approvable yet: every character lacks its sheets.
    response = _approve(api, story_id, "character:char_kiwilo")
    assert response.status_code == 409
    assert response.json()["detail"] == "Kiwilo cannot be approved yet; missing: turnaround, expressions sheet."

    # The user's choice: prompt-only consistency. The waiting stops.
    response = c.patch(_url(story_id), json={"generation_profile": {"consistency_mode": "prompt_only"}})
    assert response.status_code == 200
    assert all(not item["needs_editor"] for item in _page(api, story_id)["progress"]["characters"].values())

    # The cast step again fills only what is missing: six sheets from text alone.
    response = _post_step(api, story_id, "cast", {})
    assert response.status_code == 201, response.text
    second = response.json()
    record = api.jobs.get_job(first["id"])
    assert (record["status"], record["superseded_by"]) == ("completed", second["id"])
    assert _run(api, second["id"])["status"] == "awaiting_approval"
    assert (len(fakes.t2i.requests), len(fakes.editor.requests), len(fakes.tts.requests)) == (9, 0, 3)
    for doc in _page(api, story_id)["characters"]:
        assert doc["refs"]["turnaround"]["consistency"] == "prompt_only"
        assert doc["refs"]["expressions"]["consistency"] == "prompt_only"

    # An edited sample line makes the sample stale: approving needs a new one.
    response = c.patch(_url(story_id, "/characters/char_kiwilo"), json={"sample_line": "Le vote, c'est moi."})
    assert response.status_code == 200, response.text
    assert _page(api, story_id)["progress"]["characters"]["char_kiwilo"]["missing"] == ["sample"]
    response = _approve(api, story_id, "character:char_kiwilo")
    assert response.status_code == 409
    assert response.json()["detail"] == "Kiwilo cannot be approved yet; missing: a voice sample."
    third = _post_step(api, story_id, "cast", {}).json()
    assert _run(api, third["id"])["status"] == "awaiting_approval"
    assert fakes.tts.requests[-1] == ("edge/fr-FR-HenriNeural", "Le vote, c'est moi.")

    # Each character approved; the two leads alone do not approve the cast.
    for cid in ("char_kiwilo", "char_mangella"):
        response = _approve(api, story_id, f"character:{cid}")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "style_approved"
    assert _status(api, third["id"]) == "awaiting_approval"
    response = _approve(api, story_id, "character:char_figuette")
    assert response.status_code == 200
    story = response.json()
    assert story["status"] == "cast_approved" and story["approvals"]["cast"]
    record = api.jobs.get_job(third["id"])
    assert record["status"] == "completed" and record["approved_at"]

    # Step 6: places and props proposed, then made.
    response = _post_step(api, story_id, "places_proposal")
    assert response.status_code == 201, response.text
    proposal_job = response.json()
    assert _run(api, proposal_job["id"], FakeLLM(P0=[P0_REPLY]))["status"] == "awaiting_approval"
    page = _page(api, story_id)
    assert [item["name"] for item in page["places_proposal"]["places"]] == ["Le camp de plage",
                                                                             "Le feu d'élimination"]
    assert page["places_proposal"]["props"][0]["owner"] == "char_kiwilo"

    response = _post_step(api, story_id, "places", {})  # the saved proposal
    assert response.status_code == 201, response.text
    places_job = response.json()
    assert api.jobs.get_job(proposal_job["id"])["superseded_by"] == places_job["id"]
    llm = FakeLLM(P1=[P1_BEACH, P1_FIRE], R1=[R1_PHONE])
    assert _run(api, places_job["id"], llm)["status"] == "awaiting_approval"
    page = _page(api, story_id)
    assert [doc["place_id"] for doc in page["places"]] == [BEACH, FIRE]
    assert [doc["prop_id"] for doc in page["props"]] == [PHONE]
    assert page["progress"]["places"] == {BEACH: {"missing": []}, FIRE: {"missing": []}}
    assert page["progress"]["props"] == {PHONE: {"missing": []}}

    for doc in (f"place:{BEACH}", f"place:{FIRE}"):
        response = _approve(api, story_id, doc)
        assert response.status_code == 200 and response.json()["status"] == "cast_approved"
    response = _approve(api, story_id, f"prop:{PHONE}")
    assert response.status_code == 200 and response.json()["status"] == "places_approved"
    assert api.jobs.get_job(places_job["id"])["status"] == "completed"

    # Step 7: the season arc, then approved: the story is ready.
    response = _post_step(api, story_id, "season", {"episodes": 3})
    assert response.status_code == 201, response.text
    season_job = response.json()
    llm = FakeLLM(S1=[S1_REPLY], S2=[s2("Un."), s2("Deux."), s2("Trois.")])
    assert _run(api, season_job["id"], llm)["status"] == "awaiting_approval"
    season = _page(api, story_id)["season"]
    assert [entry["summary"] for entry in season["arc"]] == ["Un.", "Deux.", "Trois."]

    response = _approve(api, story_id, "season")
    assert response.status_code == 200, response.text
    story = response.json()
    assert story["status"] == "ready" and story["approvals"]["season"]
    assert api.jobs.get_job(season_job["id"])["status"] == "completed"
    assert _page(api, story_id)["season"]["approved_at"]
    # Every job of the story went through the one queue.
    assert api.submitted == [first["id"], second["id"], third["id"], proposal_job["id"], places_job["id"],
                             season_job["id"]]


# ============================================================ step jobs

def test_each_step_waits_for_its_precondition_and_creates_nothing(api):
    early = _story(api.store, approve=("concept", "bible"))
    for step, params in (("cast", CAST_PARAMS), ("places_proposal", None), ("places", {}), ("season", {})):
        response = _post_step(api, early, step, params)
        assert response.status_code == 409, step
    assert _post_step(api, early, "cast", CAST_PARAMS).json()["detail"] == "Approve the style first."
    assert _post_step(api, early, "season", {}).json()["detail"] == "Approve the cast first."

    story_id = _story(api.store)
    assert _post_step(api, story_id, "places_proposal").json()["detail"] == (
        "Write the cast first: places and props are proposed from it.")
    _new_character(api.store, story_id, "char_kiwilo", "Kiwilo")  # created, not written
    assert _post_step(api, story_id, "places", {}).json()["detail"] == (
        "Write at least one character first: props are drawn for the cast.")
    assert api.jobs.list_jobs() == [] and api.submitted == []


def test_the_places_need_a_proposal_or_a_list(api):
    story_id = _cast_via_api(api)
    response = _post_step(api, story_id, "places", {})
    assert response.status_code == 409 and response.json()["detail"] == "Propose or list the places first."
    response = _post_step(api, story_id, "places", {"places": P0_REPLY["places"]})
    assert response.status_code == 201, response.text


@pytest.mark.parametrize("params,needle", [
    ({"selected": ["Kiwilo", "Zorglub"]}, "'Zorglub' is not in the concept's cast sketch; pick from 'Kiwilo'"),
    ({"selected": "Kiwilo"}, "'selected' must be a list"),
    ({"custom": [{"name": "Figuette", "role": "villain", "one_line": "x"}]}, "custom[0].role"),
    ({"custom": [{"name": "", "role": "lead", "one_line": "x"}]}, "custom[0].name"),
    ({"custom": [{"name": "Figuette", "role": "lead", "one_line": "x", "age": 3}]}, "unknown key(s) age"),
    ({}, "Pick characters from the concept's cast sketch or add your own first."),
    ({"cast": ["Kiwilo"]}, "Unknown cast parameter(s) cast"),
    ({"selected": ["Kiwilo", "Mangella", "Broccolia", "Pepperino", "Avocardo"],
      "custom": [{"name": f"Figue {n}", "role": "guest", "one_line": "Une figue."} for n in range(4)]},
     "A cast has at most 8 characters: this story has 0 and the request adds 9."),
])
def test_a_bad_cast_request_is_a_400_before_any_job(api, params, needle):
    story_id = _story(api.store)
    response = _post_step(api, story_id, "cast", params)
    assert response.status_code == 400
    assert needle in json.dumps(response.json()["detail"], ensure_ascii=False)
    assert api.jobs.list_jobs() == [] and api.store.list_entities(story_id, "characters") == []


def test_eight_characters_are_a_cast_and_the_cap_counts_those_already_there(api):
    story_id = _story(api.store)
    names = ["Kiwilo", "Mangella", "Broccolia", "Pepperino", "Avocardo"]
    custom = [{"name": f"Figue {n}", "role": "guest", "one_line": "Une figue."} for n in range(3)]
    assert _post_step(api, story_id, "cast", {"selected": names, "custom": custom}).status_code == 201
    api.jobs._jobs.clear()
    for n in range(8):
        _new_character(api.store, story_id, f"char_c{n}", f"Perso {n}", role="guest")
    response = _post_step(api, story_id, "cast", {"custom": [{"name": "Un de plus", "role": "guest",
                                                              "one_line": "Encore."}]})
    assert response.status_code == 400 and "this story has 8 and the request adds 1" in response.json()["detail"]
    # Those already in the cast are simply included, and do not count twice.
    assert _post_step(api, story_id, "cast", {"custom": [{"name": "perso  1", "role": "guest",
                                                          "one_line": "x"}]}).status_code == 201


@pytest.mark.parametrize("step,params,needle", [
    ("places", {"places": [{"name": f"Lieu {n}", "one_line": "Un lieu."} for n in range(7)]},
     "places: 7 entries, at most 6"),
    ("places", {"props": [{"name": "Truc", "one_line": "Un truc.", "owner": "Zorglub"}]},
     "props[0].owner: 'Zorglub' is no character of this story"),
    ("places", {"places": [{"name": "Lieu"}]}, "places[0].one_line"),
    ("places", {"places": P0_REPLY["places"], "lieux": []}, "Unknown places parameter(s) lieux"),
    ("places_proposal", {"n": 3}, "'places_proposal' takes no parameters."),
])
def test_a_bad_places_request_is_a_400(api, step, params, needle):
    story_id = _cast_via_api(api)
    before = len(api.jobs.list_jobs())
    response = _post_step(api, story_id, step, params)
    assert response.status_code == 400
    assert needle in json.dumps(response.json()["detail"], ensure_ascii=False)
    assert len(api.jobs.list_jobs()) == before


@pytest.mark.parametrize("episodes", [2, 13, "8", True, 8.0])
def test_a_season_has_3_to_12_episodes(api, episodes):
    story_id = _story(api.store, approve=("concept", "bible", "style", "cast"))
    response = _post_step(api, story_id, "season", {"episodes": episodes})
    assert response.status_code == 400
    assert response.json()["detail"] == f"A season has 3 to 12 episodes, not {episodes!r}."
    assert _post_step(api, story_id, "season", {"episodes": 12}).status_code == 201


def test_a_cast_that_needs_images_no_link_can_make_is_refused_naming_every_link(api):
    story_id = _story(api.store)
    _settings(api, dict(NO_EDITOR, IMAGE_CHAIN="fal/flux-schnell,cloudflare/flux-1-schnell", FAL_KEY="k"))
    response = _post_step(api, story_id, "cast", CAST_PARAMS)
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert detail.startswith("No link of IMAGE_CHAIN can make a reference image on route auto: ")
    assert "fal/flux-schnell: refused: est" in detail and "allow_paid is off" in detail
    assert "cloudflare/flux-1-schnell: no API key" in detail
    assert api.jobs.list_jobs() == [] and api.submitted == []


def test_the_key_gate_comes_before_the_image_gate_and_both_before_the_queue(api):
    story_id = _story(api.store)
    _settings(api, dict(NO_EDITOR, GOOGLE_API_KEY=""))
    response = _post_step(api, story_id, "cast", CAST_PARAMS)
    assert response.status_code == 400 and "No link in the LLM chain has an API key" in response.json()["detail"]
    _settings(api, NO_EDITOR)
    api.monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    _job(api, _story(api.store), "concepts")  # another story's job fills the queue
    assert _post_step(api, story_id, "cast", CAST_PARAMS).status_code == 429


def test_one_step_at_a_time_per_story_and_no_edit_while_it_runs(api):
    story_id = _cast_via_api(api)
    queued = _post_step(api, story_id, "places_proposal").json()
    kiwi = api.store.read_entity(story_id, "characters", "char_kiwilo")

    response = _post_step(api, story_id, "cast", {})
    assert response.status_code == 409 and queued["id"] in response.json()["detail"]
    for method, path, body in (
            ("PATCH", "/characters/char_kiwilo", {"name": "Kiwi"}),
            ("DELETE", "/characters/char_kiwilo", None),
            ("POST", "/regenerate", {"target": "character:char_kiwilo:text"}),
            ("DELETE", f"/characters/char_kiwilo/uploads/{_upload_name(1)}", None)):
        response = api.client.request(method, _url(story_id, path), json=body)
        assert response.status_code == 409, (method, path, response.text)
        assert queued["id"] in response.json()["detail"]
    assert api.store.read_entity(story_id, "characters", "char_kiwilo") == kiwi
    # A places step does not touch the cast: a character is still approvable.
    assert _approve(api, story_id, "character:char_kiwilo").status_code == 200
    # A cast step does: while one runs, a character is not approved.
    from web.api.models import JobStatus

    api.jobs.set_status(queued["id"], JobStatus.FAILED)
    running = _job(api, story_id, "cast", "running")
    response = _approve(api, story_id, "character:char_mangella")
    assert response.status_code == 409 and running in response.json()["detail"]


# ========================================================== design references

def _png_bytes(size=(64, 96), colour=(20, 160, 60)):
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", size, colour).save(buf, format="PNG")
    return buf.getvalue()


def _uploads_folder(api, story_id, char_id="char_kiwilo") -> Path:
    return api.outputs / "stories" / story_id / "characters" / char_id / "refs" / "uploads"


def _upload(api, story_id, content, *, char_id="char_kiwilo", filename="ref.png", field="file"):
    return api.client.post(_url(story_id, f"/characters/{char_id}/uploads"),
                           files={field: (filename, content, "image/png")})


def test_a_png_is_accepted_stored_under_a_new_name_and_served(api):
    story_id = _story(api.store)
    _new_character(api.store, story_id, "char_kiwilo", "Kiwilo")
    content = _png_bytes()

    response = _upload(api, story_id, content, filename="../../kiwi design.png")

    assert response.status_code == 201, response.text
    entry = response.json()
    assert re.fullmatch(r"[0-9a-f]{32}\.png", entry["name"]) and entry["description"] is None
    assert entry["uploaded_at"]
    kiwi = api.store.read_entity(story_id, "characters", "char_kiwilo")
    assert kiwi["refs"]["uploads"] == [entry]
    # Only the re-encoded file is left: no temp file, the original name nowhere.
    assert [p.name for p in _uploads_folder(api, story_id).iterdir()] == [entry["name"]]
    served = api.client.get(_url(story_id, f"/media/characters/char_kiwilo/{entry['name']}"))
    assert served.status_code == 200 and served.headers["content-type"] == "image/png"
    assert served.content[:8] == b"\x89PNG\r\n\x1a\n"

    # Removed again: its entry and its file.
    response = api.client.delete(_url(story_id, f"/characters/char_kiwilo/uploads/{entry['name']}"))
    assert response.status_code == 200
    assert response.json() == {"name": entry["name"], "entry_removed": True, "file_removed": True}
    assert list(_uploads_folder(api, story_id).iterdir()) == []
    response = api.client.delete(_url(story_id, f"/characters/char_kiwilo/uploads/{entry['name']}"))
    assert response.status_code == 404 and "has no design reference" in response.json()["detail"]["message"]
    response = api.client.delete(_url(story_id, "/characters/char_kiwilo/uploads/portrait.png"))
    assert response.status_code == 400


def test_a_text_file_named_png_is_refused_415_and_leaves_nothing(api):
    pytest.importorskip("PIL")
    story_id = _story(api.store)
    _new_character(api.store, story_id, "char_kiwilo", "Kiwilo")

    response = _upload(api, story_id, b"just some text, not an image\n", filename="notes.png")

    assert response.status_code == 415
    assert response.json()["detail"] == {"message": "The file is not an image (PNG, JPEG, WebP or GIF), or it is "
                                                    "damaged."}
    assert list(_uploads_folder(api, story_id).iterdir()) == []
    assert api.store.read_entity(story_id, "characters", "char_kiwilo")["refs"]["uploads"] == []


@pytest.mark.parametrize("files,data,needle", [
    (None, {"file": "x"}, "multipart/form-data, in a field named 'file'"),
    ({"image": ("a.png", b"x", "image/png")}, None, "No image was sent."),
])
def test_a_request_without_an_image_in_file_is_a_400(api, files, data, needle):
    story_id = _story(api.store)
    _new_character(api.store, story_id, "char_kiwilo", "Kiwilo")
    url = _url(story_id, "/characters/char_kiwilo/uploads")
    response = api.client.post(url, files=files, data=data) if files else api.client.post(url, json=data)
    assert response.status_code == 400 and needle in response.json()["detail"]["message"]
    assert list(_uploads_folder(api, story_id).iterdir()) == []


def _asgi_upload(api, story_id, chunks, headers):
    """POST to the upload route straight through ASGI, the body served one
    chunk at a time as the app asks for it. ``(status, chunks the app read)``."""
    path = f"/api/stories/{story_id}/characters/char_kiwilo/uploads"
    served = {"n": 0}
    chunks = iter(chunks)

    async def receive():
        chunk = next(chunks, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        served["n"] += 1
        return {"type": "http.request", "body": chunk, "more_body": True}

    sent = []

    async def send(message):
        sent.append(message)

    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "POST", "scheme": "http",
        "path": path, "raw_path": path.encode(), "query_string": b"", "root_path": "",
        "headers": [(b"host", b"testserver")] + [(k.encode(), v.encode()) for k, v in headers.items()],
        "client": ("testclient", 50000), "server": ("testserver", 80),
    }
    asyncio.run(api.app(scope, receive, send))
    start = next(message for message in sent if message["type"] == "http.response.start")
    return start["status"], served["n"]


def _eleven_mib_chunks(count):
    yield (b'--xyz\r\nContent-Disposition: form-data; name="file"; filename="big.png"\r\n'
           b"Content-Type: image/png\r\n\r\n")
    for _ in range(count):
        yield b"\x00" * MIB


def test_an_oversized_upload_is_refused_413_without_reading_or_buffering_its_body(api):
    story_id = _story(api.store)
    _new_character(api.store, story_id, "char_kiwilo", "Kiwilo")
    content_type = "multipart/form-data; boundary=xyz"

    # Declared: 11 MB is refused before a single byte of the body is read.
    status, read = _asgi_upload(api, story_id, _eleven_mib_chunks(11),
                                {"content-type": content_type, "content-length": str(11 * MIB + 200)})
    assert (status, read) == (413, 0)

    # Undeclared (chunked): the stream is left the moment the image passes 10 MB,
    # never read to its end (60 MB here).
    status, read = _asgi_upload(api, story_id, _eleven_mib_chunks(60), {"content-type": content_type})
    assert status == 413 and read <= 12
    assert list(_uploads_folder(api, story_id).iterdir()) == []

    # Through the client, the 413 says why.
    response = _upload(api, story_id, b"\x00" * (11 * MIB))
    assert response.status_code == 413
    assert response.json()["detail"] == {"message": "The image is larger than 10 MB."}
    assert api.store.read_entity(story_id, "characters", "char_kiwilo")["refs"]["uploads"] == []


def test_an_upload_waits_for_the_running_step_and_a_fifth_is_refused_unread(api):
    story_id = _story(api.store)
    four = [{"name": _upload_name(n), "description": None, "uploaded_at": NOW} for n in range(1, 5)]
    _new_character(api.store, story_id, "char_kiwilo", "Kiwilo", uploads=four[:3])
    job_id = _job(api, story_id, "cast", "running")

    response = _upload(api, story_id, b"whatever")
    assert response.status_code == 409 and job_id in response.json()["detail"]
    assert not _uploads_folder(api, story_id).exists()

    api.jobs._jobs.clear()
    doc = api.store.read_entity(story_id, "characters", "char_kiwilo")
    doc["refs"]["uploads"] = four
    api.store.write_entity(story_id, "characters", doc, now=NOW)
    status, read = _asgi_upload(api, story_id, _eleven_mib_chunks(3),
                                {"content-type": "multipart/form-data; boundary=xyz"})
    assert (status, read) == (400, 0)
    response = _upload(api, story_id, b"whatever")
    assert response.json()["detail"] == {"message": "Kiwilo already has 4 design references; remove one first."}
    assert api.client.post(_url(story_id, "/characters/char_nobody/uploads"),
                           files={"file": ("a.png", b"x", "image/png")}).status_code == 404


# ================================================================ regenerate

def test_each_phase_2_step_and_target_is_a_job_of_its_document(api):
    doc = api.routes._job_doc
    assert [doc(step, {}) for step in ("cast", "places_proposal", "places", "season")] == [
        "cast", "places", "places", "season"]
    for target, expected in (
            ("character:char_kiwilo:text", "character:char_kiwilo"),
            ("character:char_kiwilo:image:turnaround", "character:char_kiwilo"),
            ("character:char_kiwilo:voice", "character:char_kiwilo"),
            (f"place:{BEACH}:image:night", f"place:{BEACH}"), (f"place:{BEACH}:text", f"place:{BEACH}"),
            (f"prop:{PHONE}:image", f"prop:{PHONE}"), ("season:2", "season"),
            ("character:char_kiwilo:image:extra:1", None), ("bible:tone", "bible"), ("concepts", "concepts")):
        assert doc("regenerate", {"target": target}) == expected, target


def test_a_regenerate_job_carries_target_note_and_voice_and_supersedes_its_entitys(api):
    story_id = _full_via_api(api)
    older = _job(api, story_id, "regenerate", "awaiting_approval", params={"target": "character:char_kiwilo:text"})
    other = _job(api, story_id, "regenerate", "awaiting_approval",
                 params={"target": "character:char_mangella:image:portrait"})
    season = _job(api, story_id, "season", "awaiting_approval", params={"episodes": 3})
    cast = [job["id"] for job in api.jobs.list_step_jobs(story_id) if job["step"] == "cast"]

    response = api.client.post(_url(story_id, "/regenerate"),
                               json={"target": "character:char_kiwilo:image:portrait", "note": "plus vieux"})

    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["step"], job["params"]) == ("regenerate", {"target": "character:char_kiwilo:image:portrait",
                                                           "note": "plus vieux", "voice": None})
    assert api.jobs.get_job(older)["superseded_by"] == job["id"]
    assert _status(api, other) == _status(api, season) == "awaiting_approval"
    assert all(_status(api, job_id) == "awaiting_approval" for job_id in cast)

    # Run: the portrait and both sheets again; approving Kiwilo completes the job.
    assert _run(api, job["id"])["status"] == "awaiting_approval"
    assert _approve(api, story_id, "character:char_kiwilo").status_code == 200
    assert _status(api, job["id"]) == "completed" and _status(api, other) == "awaiting_approval"

    # An arc entry is a job of the season: it supersedes the season step's.
    story_season = {"$schema": "season_arc_v1", "episodes_planned": 3, "approved_at": None, "updated_at": NOW,
                    "arc": [{"ep": n, "function": "setup", "summary": f"Épisode {n}.", "open_hooks_in": [],
                             "open_hooks_out": [], "characters": []} for n in (1, 2, 3)],
                    "series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}},
                    "audience_feedback": []}
    api.store.write_doc(story_id, "season.json", story_season, now=NOW)
    response = api.client.post(_url(story_id, "/regenerate"), json={"target": "season:2"})
    assert response.status_code == 201, response.text
    assert api.jobs.get_job(season)["superseded_by"] == response.json()["id"]


@pytest.mark.parametrize("target,status,needle", [
    ("character:char_nobody:text", 404, "This story has no character 'char_nobody'."),
    ("place:place_nowhere:image:day", 404, "This story has no place 'place_nowhere'."),
    ("prop:prop_nothing:text", 404, "This story has no prop 'prop_nothing'."),
    ("season:1", 409, "There is no season arc yet: write the season first."),
    ("character:char_kiwilo:nope", 400, "character:<char_id>:image:portrait|turnaround|expressions"),
    ("place:place_x:image:sunset", 400, "place:<place_id>:image:day|night|dusk|rain|dawn"),
    ("character:char_kiwilo:image:extra:1", 400, "Regenerating 'character:char_kiwilo:image:extra:1' arrives in "
                                                 "a later phase."),
    (f"place:{BEACH}:image:night", 409, "Make Le camp de plage's day plate first"),
])
def test_a_regenerate_target_is_checked_against_the_story_before_any_job(api, target, status, needle):
    story_id = _cast_via_api(api)
    doc = {"$schema": "place_v1", "place_id": BEACH, "name": "Le camp de plage", "one_line": "Le camp.",
           "descriptor": "a crescent of white sand", "layout_notes": None, "time_variants": {"day": None},
           "prompt_block": None, "approved_at": None, "created_at": NOW, "updated_at": NOW}
    api.store.write_entity(story_id, "places", doc, now=NOW)
    before = len(api.jobs.list_jobs())
    response = api.client.post(_url(story_id, "/regenerate"), json={"target": target})
    assert response.status_code == status, response.text
    assert needle in response.json()["detail"]
    assert len(api.jobs.list_jobs()) == before


def test_an_image_regenerate_meets_the_chain_it_needs_before_any_job(api):
    story_id = _cast_via_api(api)  # sheets made with the local editor
    _new_character(api.store, story_id, "char_zorglub", "Zorglub", role="guest")
    _settings(api, NO_EDITOR)

    # A sheet in references mode needs the editor: none can run.
    response = api.client.post(_url(story_id, "/regenerate"), json={"target": "character:char_kiwilo:image:turnaround"})
    assert response.status_code == 409
    assert response.json()["detail"].startswith("No link of IMAGE_EDIT_CHAIN can make a reference image")
    assert "switch the story to prompt-only consistency" in response.json()["detail"]
    # A portrait is text to image: the sheets that cannot be redrawn are recorded, not refused.
    assert api.client.post(_url(story_id, "/regenerate"),
                           json={"target": "character:char_kiwilo:image:portrait"}).status_code == 201
    api.jobs._jobs.clear()
    # No image of a character that is not written; no text-to-image link, no portrait.
    response = api.client.post(_url(story_id, "/regenerate"), json={"target": "character:char_zorglub:image:portrait"})
    assert response.status_code == 409 and response.json()["detail"] == (
        "Write Zorglub first: its text makes every image.")
    _settings(api, dict(NO_EDITOR, IMAGE_CHAIN="cloudflare/flux-1-schnell"))
    response = api.client.post(_url(story_id, "/regenerate"), json={"target": "character:char_kiwilo:image:portrait"})
    assert response.status_code == 409 and "cloudflare/flux-1-schnell: no API key" in response.json()["detail"]
    # A voice calls neither the LLM nor an image chain: no gate but its own checks.
    _settings(api, dict(NO_EDITOR, GOOGLE_API_KEY="", IMAGE_CHAIN="cloudflare/flux-1-schnell"))
    response = api.client.post(_url(story_id, "/regenerate"), json={"target": "character:char_kiwilo:voice"})
    assert response.status_code == 201
    assert [job["params"]["target"] for job in api.jobs.list_step_jobs(story_id, step="regenerate")] == [
        "character:char_kiwilo:voice"]


def test_a_picked_voice_is_one_of_the_languages_and_no_other_leads(api):
    story_id = _cast_via_api(api)
    url = _url(story_id, "/regenerate")
    kiwilo = "character:char_kiwilo:voice"

    response = api.client.post(url, json={"target": kiwilo, "voice": {"provider": "edge",
                                                                      "voice_id": "fr-FR-DeniseNeural"}})
    assert response.status_code == 409
    assert response.json()["detail"] == ("edge/fr-FR-DeniseNeural is already Mangella's voice: no two leads or "
                                         "supports share a voice; pick another for Kiwilo.")
    response = api.client.post(url, json={"target": kiwilo, "voice": {"provider": "edge",
                                                                      "voice_id": "en-US-GuyNeural"}})
    assert response.status_code == 400
    assert response.json()["detail"].startswith("edge/en-US-GuyNeural is not a fr voice TTS_CHAIN can reach; "
                                                "pick one of: edge/fr-FR-")
    for body in ({"target": kiwilo, "voice": {"provider": "edge"}},
                 {"target": kiwilo, "voice": {"provider": "edge", "voice_id": "fr-CA-ThierryNeural", "rate": "fast"}},
                 {"target": kiwilo, "voice": {"provider": "edge", "voice_id": "fr-CA-ThierryNeural", "age": 3}},
                 {"target": "character:char_kiwilo:text", "voice": {"provider": "edge", "voice_id": "x"}},
                 {"target": "bible:tone", "voice": {"provider": "edge", "voice_id": "x"}}):
        assert api.client.post(url, json=body).status_code == 400, body
    assert api.jobs.list_step_jobs(story_id, step="regenerate") == []

    thierry = {"provider": "edge", "voice_id": "fr-CA-ThierryNeural", "rate": "+10%"}
    response = api.client.post(url, json={"target": kiwilo, "voice": thierry})
    assert response.status_code == 201, response.text
    job = response.json()
    assert job["params"] == {"target": kiwilo, "note": None, "voice": thierry}
    assert _run(api, job["id"])["status"] == "awaiting_approval"
    kiwi = api.store.read_entity(story_id, "characters", "char_kiwilo")
    assert (kiwi["voice"]["voice_id"], kiwi["voice"]["rate"]) == ("fr-CA-ThierryNeural", "+10%")
    assert api.fakes.tts.requests[-1][0] == "edge/fr-CA-ThierryNeural"


# ================================================= the story page, approvals

def test_the_story_page_asks_the_local_editor_whether_it_is_there_once_a_minute(api):
    """Found in the browser review: with ComfyUI down the page said the
    editor was ready ("probed when it runs"), the banner with the
    prompt-only switch never showed, and the user learnt it from the job log
    after Continue. The page and the cast estimate now ask -- one short
    status probe per server a minute, while the page is polled."""
    from clipping.providers import local_comfyui

    story_id = _cast_via_api(api, settings=NO_EDITOR)  # portraits made, every sheet missing
    _settings(api, dict(EDITOR, LOCAL_COMFYUI_URL="http://comfy.test:8188"))
    clock = [1000.0]
    api.monkeypatch.setattr(local_comfyui, "_now", lambda: clock[0])
    down = ComfyStatus(up=False)
    api.monkeypatch.setattr(local_comfyui, "urllib_transport", down)

    page = _page(api, story_id)

    readiness = page["progress"]["edit_readiness"]
    assert (readiness["ready"], readiness["route_class"], readiness["link"]) == (False, "blocked", None)
    [row] = readiness["links"]
    assert (row["link"], row["status"]) == ("local/comfyui", "skipped")
    assert row["reason"] == ("unreachable at http://comfy.test:8188 (GET http://comfy.test:8188/system_stats: "
                             "[Errno 111] Connection refused)")
    assert readiness["message"] == (
        f"No link of IMAGE_EDIT_CHAIN can make a reference image on route auto: local/comfyui: {row['reason']}.")
    assert page["progress"]["characters"] == {cid: {"missing": ["turnaround", "expressions"], "needs_editor": True}
                                              for cid in IDS}

    # The next polls, the cast estimate (ContinueCast) and a new story's
    # (NoCastYet reads its `edit`) give the same verdict without asking again.
    clock[0] += 4.0
    assert _page(api, story_id)["progress"]["edit_readiness"] == readiness
    cast = _estimate(api, story_id, "cast")
    assert cast["edit"]["ready"] is False and cast["edit"]["links"] == [row]
    assert "need an editor or prompt-only consistency" in cast["message"]
    fresh = _story(api.store)
    sketch = _estimate(api, fresh, "cast", selected=["Kiwilo"])
    assert sketch["edit"]["ready"] is False and sketch["edit"]["links"] == [row]
    assert down.urls == ["http://comfy.test:8188/system_stats"]
    assert down.timeouts[0] <= 2.0

    # ComfyUI comes up: the first poll after the minute sees it.
    up = ComfyStatus(up=True)
    api.monkeypatch.setattr(local_comfyui, "urllib_transport", up)
    clock[0] += 57.0
    readiness = _page(api, story_id)["progress"]["edit_readiness"]
    assert (readiness["ready"], readiness["route_class"], readiness["link"]) == (True, "local", "local/comfyui")
    assert all(not item["needs_editor"] for item in _page(api, story_id)["progress"]["characters"].values())
    assert up.urls == ["http://comfy.test:8188/system_stats"]
    # Nothing was generated or spent by asking.
    assert api.fakes.editor.requests == [] and api.fakes.editor.probes == []


def test_approvals_refuse_unknown_entities_and_a_season_that_is_not_complete(api):
    story_id = _cast_via_api(api)
    for doc in ("character:char_nobody", "place:place_nowhere", "prop:prop_nothing", "character:BAD",
                "character:", "cast", "places"):
        assert _approve(api, story_id, doc).status_code == 404, doc
    response = _approve(api, story_id, "season")
    assert response.status_code == 409
    assert response.json()["detail"] == "Approve the cast, the places and the props first."

    places_ready = _story(api.store, approve=("concept", "bible", "style", "cast", "places"))
    response = _approve(api, places_ready, "season")
    assert response.status_code == 409 and "run the season step first" in response.json()["detail"]
    skeleton = {"$schema": "season_arc_v1", "episodes_planned": 3, "arc": [], "approved_at": None,
                "series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}},
                "audience_feedback": [], "updated_at": NOW}
    api.store.write_doc(places_ready, "season.json", skeleton, now=NOW)
    response = _approve(api, places_ready, "season")
    assert response.status_code == 409
    assert response.json()["detail"] == "The season arc is not complete: 0 of 3 episodes have a summary."
    assert api.store.get(places_ready)["status"] == "places_approved"


# ============================================================= inline edits

def _approve_all(api, story_id):
    for doc in ("character:char_kiwilo", "character:char_mangella", "character:char_figuette",
                f"place:{BEACH}", f"place:{FIRE}", f"prop:{PHONE}"):
        assert _approve(api, story_id, doc).status_code == 200, doc
    assert api.store.get(story_id)["status"] == "places_approved"


def test_an_edit_clears_that_entitys_approval_and_drops_the_group_status(api):
    story_id = _full_via_api(api)
    _approve_all(api, story_id)
    lock = api.store.read_doc(story_id, "style_lock.json")
    mango_before = api.store.read_entity(story_id, "characters", "char_mangella")

    response = api.client.patch(_url(story_id, "/characters/char_kiwilo"),
                                json={"descriptor": "  an older kiwi with greying fuzz and a cane  ",
                                      "personality": {"wants": "Gagner une dernière fois."}})

    assert response.status_code == 200, response.text
    kiwi = response.json()
    assert kiwi["descriptor"] == "an older kiwi with greying fuzz and a cane"
    assert kiwi["prompt_block"] == prompting.character_prompt_block(
        lock, descriptor=kiwi["descriptor"], signature_items=kiwi["signature_items"])
    assert kiwi["personality"]["wants"] == "Gagner une dernière fois."
    assert kiwi["personality"]["traits"] == ["rusé", "charmeur"]  # merged, not replaced
    assert kiwi["approved_at"] is None
    story = api.store.get(story_id)
    assert story["approvals"]["cast"] is None and story["status"] == "style_approved"
    assert api.store.read_entity(story_id, "characters", "char_mangella") == mango_before

    # A place and a prop: the same rule, their own prompt blocks.
    response = api.client.patch(_url(story_id, f"/places/{BEACH}"), json={"layout_notes": "the sea on the left"})
    assert response.status_code == 200
    beach = response.json()
    assert beach["prompt_block"] == prompting.place_prompt_block(
        lock, descriptor=P1_BEACH["descriptor"], layout_notes="the sea on the left")
    assert beach["approved_at"] is None and api.store.get(story_id)["approvals"]["places"] is None
    response = api.client.patch(_url(story_id, f"/props/{PHONE}"), json={"owner_char_id": "char_mangella"})
    assert response.status_code == 200 and response.json()["owner_char_id"] == "char_mangella"
    response = api.client.patch(_url(story_id, f"/props/{PHONE}"), json={"descriptor": "a cracked coconut"})
    assert response.json()["prompt_block"] == prompting.prop_prompt_block(lock, descriptor="a cracked coconut")


def test_an_edit_the_rules_refuse_is_a_400_and_changes_nothing(api):
    story_id = _full_via_api(api)
    kiwi = api.store.read_entity(story_id, "characters", "char_kiwilo")
    url = _url(story_id, "/characters/char_kiwilo")
    for body, needle in (
            ({"signature_items": ["a", "b", "c", "d"]}, "$.signature_items"),
            ({"role": "villain"}, "$.role"),
            ({"name": "mangella"}, "another character is already named 'mangella'"),
            ({"descriptor": " ".join(["word"] * 46)}, "46 words"),
            ({"rate": "fast"}, "$.voice.rate"),
            ({"name": None}, "$.name"),
            ({"personality": {"mood": "sombre"}}, "$.personality"),
    ):
        response = api.client.patch(url, json=body)
        assert response.status_code == 400, body
        detail = response.json()["detail"]
        assert detail["message"] == "The character would not be valid with these values."
        assert any(needle in error for error in detail["errors"]), (body, detail)
    response = api.client.patch(_url(story_id, f"/props/{PHONE}"), json={"owner_char_id": "char_nobody"})
    assert response.status_code == 400 and "no character of this story" in response.json()["detail"]["errors"][0]
    assert api.store.read_entity(story_id, "characters", "char_kiwilo") == kiwi
    # Nothing sent: nothing written, the entity as it is.
    response = api.client.patch(url, json={})
    assert response.status_code == 200 and response.json() == kiwi
    assert api.client.patch(_url(story_id, "/characters/char_nobody"), json={"name": "x"}).status_code == 404
    assert api.client.patch(_url(story_id, "/places/place_nowhere"), json={"name": "x"}).status_code == 404


def test_the_voice_fields_edit_the_pinned_voice_and_brief_and_drop_the_stale_sample(api):
    story_id = _cast_via_api(api)
    url = _url(story_id, "/characters/char_mangella")
    sample = _url(story_id, "/media/characters/char_mangella/voice_sample.mp3")
    assert api.client.get(sample).status_code == 200

    response = api.client.patch(url, json={"voice_direction": "cold, slow"})
    assert response.status_code == 200
    assert api.client.get(sample).status_code == 200  # the direction does not change the audio
    response = api.client.patch(url, json={"sample_line": "Tu vas perdre.", "pitch": "-5Hz"})
    assert response.status_code == 200, response.text
    mango = response.json()
    assert (mango["voice"]["sample_line"], mango["voice_hints"]["sample_line"]) == ("Tu vas perdre.",) * 2
    assert (mango["voice"]["direction"], mango["voice"]["pitch"]) == ("cold, slow", "-5Hz")
    assert api.client.get(sample).status_code == 404

    _new_character(api.store, story_id, "char_zorglub", "Zorglub", role="guest")
    response = api.client.patch(_url(story_id, "/characters/char_zorglub"), json={"sample_line": "Salut."})
    assert response.status_code == 409 and "has no voice brief yet" in response.json()["detail"]
    response = api.client.patch(_url(story_id, "/characters/char_zorglub"), json={"rate": "+5%"})
    assert response.status_code == 409 and "has no pinned voice yet" in response.json()["detail"]


def test_the_voice_picker_lists_the_pinned_voice_alternates_and_what_others_took(api):
    story_id = _cast_via_api(api)
    kiwilo = api.store.read_entity(story_id, "characters", "char_kiwilo")
    mangella = api.store.read_entity(story_id, "characters", "char_mangella")
    figuette = api.store.read_entity(story_id, "characters", "char_figuette")
    # Three characters, eight fr voices in the catalogue: each got its own.
    assert kiwilo["voice"] and mangella["voice"] and figuette["voice"]

    response = api.client.get(_url(story_id, "/characters/char_kiwilo/voices"))
    assert response.status_code == 200, response.text
    body = response.json()

    assert body["pinned"] == {"provider": kiwilo["voice"]["provider"], "voice_id": kiwilo["voice"]["voice_id"]}
    assert 0 < len(body["alternates"]) <= 6
    keys = [(a["provider"], a["voice_id"]) for a in body["alternates"]]
    assert len(keys) == len(set(keys))  # nothing offered twice
    assert (body["pinned"]["provider"], body["pinned"]["voice_id"]) not in keys
    for entry in body["alternates"]:
        assert set(entry) == {"provider", "voice_id", "lang", "gender", "age", "style_tags", "link"}
        assert entry["lang"].lower().startswith("fr")

    others = {(mangella["voice"]["provider"], mangella["voice"]["voice_id"]),
              (figuette["voice"]["provider"], figuette["voice"]["voice_id"])}
    assert set(body["taken"]) == {f"{p}/{v}" for p, v in others}
    assert f"{kiwilo['voice']['provider']}/{kiwilo['voice']['voice_id']}" not in body["taken"]
    assert not (others & set(keys))  # a voice another lead/support already has is never offered

    assert api.client.get(_url(story_id, "/characters/char_nobody/voices")).status_code == 404


def test_deleting_an_entity_refolds_the_group_approval(api):
    story_id = _cast_via_api(api)
    for cid in ("char_kiwilo", "char_mangella"):
        assert _approve(api, story_id, f"character:{cid}").status_code == 200
    assert api.store.get(story_id)["approvals"]["cast"] is None  # Figuette, a support, is not approved

    response = api.client.delete(_url(story_id, "/characters/char_figuette"))

    assert response.status_code == 200, response.text
    body = response.json()
    assert (body["message"], body["id"], body["kept"]) == ("Character deleted", "char_figuette", [])
    assert body["removed"] == [f"outputs/stories/{story_id}/characters/char_figuette/"]
    story = api.store.get(story_id)
    assert story["status"] == "cast_approved" and story["cast_ids"] == ["char_kiwilo", "char_mangella"]
    assert not (api.outputs / "stories" / story_id / "characters" / "char_figuette").exists()
    assert api.client.delete(_url(story_id, "/characters/char_figuette")).status_code == 404
    assert api.client.delete(_url(story_id, "/places/place_nowhere")).status_code == 404
    assert api.client.delete(_url(story_id, "/props/prop_nothing")).status_code == 404


# ===================================================================== media

def test_the_media_route_serves_an_entitys_images_and_sample_and_nothing_else(api, tmp_path):
    story_id = _full_via_api(api)
    folder = api.outputs / "stories" / story_id / "characters" / "char_kiwilo"
    media = _url(story_id, "/media")

    response = api.client.get(f"{media}/characters/char_kiwilo/portrait.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png" and response.headers["cache-control"] == "no-store"
    assert response.content == (folder / "refs" / "portrait.png").read_bytes()
    response = api.client.get(f"{media}/characters/char_kiwilo/voice_sample.mp3")
    assert response.status_code == 200 and response.headers["content-type"] == "audio/mpeg"
    assert response.content.startswith(b"ID3fake")
    assert api.client.get(f"{media}/places/{BEACH}/variant_day.png").status_code == 200
    assert api.client.get(f"{media}/props/{PHONE}/image.png").status_code == 200

    other = _story(api.store)
    for path in (
            f"{media}/characters/char_kiwilo/..%2F..%2F..%2Fstory.json",
            f"{media}/characters/char_kiwilo/%2E%2E",
            f"{media}/characters/char_kiwilo/character.json",
            f"{media}/characters/char_kiwilo/portrait.gif",
            f"{media}/characters/char_kiwilo/secret.png",
            f"{media}/characters/char_kiwilo/variant_day.png",
            f"{media}/places/char_kiwilo/portrait.png",
            f"{media}/stories/char_kiwilo/portrait.png",
            f"{media}/styles/preview/preview_1.png",
            f"{media}/characters/..%2F{story_id}/portrait.png",
            f"{media}/characters/char_nobody/portrait.png",
            _url(other, "/media/characters/char_kiwilo/portrait.png"),
            _url(UNKNOWN_ID, "/media/characters/char_kiwilo/portrait.png"),
            "/api/stories/..%2Fstories/media/characters/char_kiwilo/portrait.png",
    ):
        assert api.client.get(path).status_code == 404, path

    # A symlink in a file's place, or in a folder's, is never followed.
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    portrait = folder / "refs" / "portrait.png"
    portrait.unlink()
    portrait.symlink_to(outside)
    assert api.client.get(f"{media}/characters/char_kiwilo/portrait.png").status_code == 404
    refs = api.outputs / "stories" / story_id / "places" / FIRE / "refs"
    moved = tmp_path / "moved-refs"
    refs.rename(moved)
    refs.symlink_to(moved, target_is_directory=True)
    assert api.client.get(f"{media}/places/{FIRE}/variant_day.png").status_code == 404


# ================================================================= estimates

def _estimate(api, story_id, step, **params):
    response = api.client.get(_url(story_id, f"/estimate/{step}"), params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_the_cast_estimate_on_the_free_route_is_free_and_says_the_sheets_need_an_editor(api, tmp_path):
    story_id = _story(api.store)

    body = _estimate(api, story_id, "cast", selected=["Kiwilo", "Mangella"])

    assert body["units"] == {"llm_calls": 2, "images": 2, "edit_images": 4, "tts_chars": 240}
    assert (body["step"], body["est_usd"], body["route_class"], body["link"], body["ready"]) == (
        "cast", 0.0, "free", "pollinations/flux", True)
    assert body["edit"]["ready"] is False and body["edit"]["units"] == {"images": 4}
    assert "need an editor or prompt-only consistency" in body["message"]
    assert "fal/seedream-4-edit: no API key (FAL_KEY is not set)" in body["message"]
    # An estimate calls nothing and spends nothing.
    assert api.fakes.t2i.requests == [] and not (tmp_path / "data" / "spend.json").exists()

    # In prompt-only mode the sheets are text to image, still free.
    api.client.patch(_url(story_id), json={"generation_profile": {"consistency_mode": "prompt_only"}})
    body = _estimate(api, story_id, "cast", selected=["Kiwilo"])
    assert body["units"] == {"llm_calls": 1, "images": 3, "edit_images": 0, "tts_chars": 120}
    assert body["est_usd"] == 0.0 and body["ready"] is True

    response = api.client.get(_url(story_id, "/estimate/cast"), params={"selected": ["Zorglub"]})
    assert response.status_code == 400 and "'Zorglub' is not in the concept's cast sketch" in response.json()["detail"]


def test_the_cast_estimate_counts_only_what_is_missing(api):
    story_id = _cast_via_api(api, settings=NO_EDITOR)  # portraits, voices, samples; no sheet
    body = _estimate(api, story_id, "cast")
    assert body["units"] == {"llm_calls": 0, "images": 0, "edit_images": 6, "tts_chars": 0}
    body = _estimate(api, story_id, "cast", selected=["Kiwilo", "Pepperino"])  # Kiwilo is there already
    assert body["units"] == {"llm_calls": 1, "images": 1, "edit_images": 8, "tts_chars": 120}


def test_the_cast_estimate_on_a_v2_story_points_to_the_quality_keys_not_prompt_only(api):
    """DEC-221/TASK 2 (phase 7 stage 2a): a v2 story has no prompt-only switch
    to fall back to (store._merge_generation_profile), so its stop-and-ask
    must say no quality image link can run and point to the quality keys
    (refimages.quality_advice/editor_advice), never the legacy "need an
    editor or prompt-only consistency" sentence meant for a story that does
    have that switch."""
    story_id = _cast_via_api(api, settings=NO_EDITOR)  # portraits, voices, samples; no sheet
    response = api.client.patch(_url(story_id), json={
        "generation_profile": {"pipeline": "v2", "budget_profile": "quality"}})
    assert response.status_code == 200, response.text

    body = _estimate(api, story_id, "cast")

    # Phase 7 stage 3a (DEC-226) and 5a (DEC-228): a v2 run writes each
    # character's dossier (D1) and look (D2) before its sheets, and the
    # estimate counts those 3 x 2 calls. 2026-10-02 (the switch's hint made
    # true): re-pinned on purpose -- the 3 portraits, drawn before the looks,
    # are drawn again from them (cast.apply_d2), so they are counted too, and
    # the quality portrait link's refusal (no FAL_KEY) is the estimate's.
    assert body["units"] == {"llm_calls": 6, "images": 3, "edit_images": 6, "tts_chars": 0}
    assert body["ready"] is False and body["edit"]["ready"] is False
    assert "prompt-only" not in body["message"] and "prompt_only" not in body["message"]
    assert "FAL_KEY" in body["message"] and "GEMINI_PAID_API_KEY" not in body["message"]

    # Once the looks are written over the portraits they were drawn from, only
    # the sheets wait: the edit's stop-and-ask.
    for doc in api.store.list_entities(story_id, "characters"):
        doc["look"] = schemas.d2_look({
            "build": "lean human body", "silhouette": "upright", "face": "round fruit head", "hair": "none",
            "skin_material": "fruit skin", "height_cm": 170, "palette": ["green"],
            "wardrobe_sets": [{"id": "daily", "context": "every day", "items": "a suit"}], "season_change": ""})
        doc["dossier"] = {"backstory": "Né sur l'île.", "goal": "Gagner.", "need": "Confiance.", "fears": "Perdre.",
                          "secrets": [], "relationships": [], "arc": "Du menteur au loyal.",
                          "voice": {"patterns": "Court.", "vocabulary": "Simple.", "catchphrases": []}}
        api.store.write_entity(story_id, "characters", doc, now=NOW)
    body = _estimate(api, story_id, "cast")
    assert body["units"] == {"llm_calls": 0, "images": 0, "edit_images": 6, "tts_chars": 0}
    assert body["edit"]["ready"] is False
    assert "need an editor or prompt-only consistency" not in body["message"]
    assert "prompt-only" not in body["message"] and "prompt_only" not in body["message"]
    assert "no quality image link can run" in body["message"]
    # Re-pinned (stage 2c, DEC-235: "fal only"): the quality sheet role's
    # IMAGE_EDIT link is fal/seedream-4.5-edit alone now, and QUALITY_KEYS is
    # FAL_KEY alone, so the stop-and-ask never names GEMINI_PAID_API_KEY.
    assert "FAL_KEY" in body["message"] and "GEMINI_PAID_API_KEY" not in body["message"]
    assert "allow paid providers" in body["message"]


def test_the_cast_estimate_counts_existing_missing_sheets_as_images_once_switched_to_prompt_only(api):
    """Regression lock for the Tier-2 walk finding "prompt-only sheets
    estimated as edits": a cast already stalled on missing sheets in
    references mode, then switched to prompt-only, must have its *existing*
    missing sheets re-priced as images too (workflow.cast_units already
    re-reads generation_profile.consistency_mode on every call; the walk's
    actual bug was the dashboard's cast estimate chip not refetching after
    the switch -- see CastStep.jsx's ContinueCast/ImageSlot fixes)."""
    story_id = _cast_via_api(api, settings=NO_EDITOR)  # portraits, voices, samples; no sheet (references)
    api.client.patch(_url(story_id), json={"generation_profile": {"consistency_mode": "prompt_only"}})
    body = _estimate(api, story_id, "cast")
    assert body["units"] == {"llm_calls": 0, "images": 6, "edit_images": 0, "tts_chars": 0}


def test_a_paid_editor_is_refused_with_its_numbers_while_allow_paid_is_off_and_priced_once_on(api):
    story_id = _story(api.store)
    _settings(api, PAID_EDITOR)

    off = _estimate(api, story_id, "cast", selected=["Kiwilo", "Mangella"])

    assert off["edit"]["ready"] is False and off["est_usd"] == 0.0 and off["ready"] is True
    [row] = off["edit"]["links"]
    assert row == {"link": "fal/seedream-4-edit", "status": "skipped", "paid": True,
                   "est_usd": round(4 * SEEDREAM_PER_EDIT, 6), "reason": row["reason"]}
    assert row["reason"].startswith("refused: est $0.120 on fal/seedream-4-edit; allow_paid is off")

    _settings(api, dict(PAID_EDITOR, ALLOW_PAID="1"))
    on = _estimate(api, story_id, "cast", selected=["Kiwilo", "Mangella"])

    assert on["edit"]["ready"] is True and on["edit"]["route_class"] == "paid"
    assert on["est_usd"] == round(4 * SEEDREAM_PER_EDIT, 6)  # the portraits are free: images x $0
    assert "paid: est $0.120" in on["message"]

    # The story's total counts against its cap.
    _settings(api, dict(PAID_EDITOR, ALLOW_PAID="1", PER_STORY_CAP_USD="0.5"))
    from clipping.aistory.ledger import CostLedger

    CostLedger(str(api.outputs / "stories" / story_id / "cost_ledger.json")).append(
        step="character_image:x:portrait", provider="fal", model="m", unit="image", qty=1, est_usd=0.45, paid=True)
    capped = _estimate(api, story_id, "cast", selected=["Kiwilo", "Mangella"])
    assert capped["edit"]["ready"] is False and capped["est_usd"] == 0.0
    assert "this story to $0.57 of its $0.50 cap" in capped["edit"]["links"][0]["reason"]


def test_the_places_season_and_proposal_estimates(api):
    story_id = _cast_via_api(api)

    body = _estimate(api, story_id, "places")
    assert (body["ready"], body["message"]) == (False, "Propose or list the places first.")
    proposal = {"$schema": "places_proposal_v1", "places": P0_REPLY["places"],
                "props": [{"name": "Le coco-téléphone", "one_line": "Il sonne.", "owner": None}], "updated_at": NOW}
    api.store.write_doc(story_id, "places_proposal.json", proposal, now=NOW)
    body = _estimate(api, story_id, "places")
    assert body["units"] == {"llm_calls": 3, "images": 3, "edit_images": 0, "tts_chars": 0}
    assert (body["est_usd"], body["route_class"], body["ready"]) == (0.0, "free", True)

    assert _estimate(api, story_id, "places_proposal")["units"] == {"llm_calls": 1}
    assert _estimate(api, story_id, "season")["units"] == {"llm_calls": 9}
    season = _estimate(api, story_id, "season", episodes=5)
    assert season["units"] == {"llm_calls": 6} and season["route_class"] == "free" and season["ready"]
    assert api.client.get(_url(story_id, "/estimate/season"), params={"episodes": 2}).status_code == 400

    # A per-item regenerate: a text is one call, a portrait an image and its sheets again.
    regen = _url(story_id, "/estimate/regenerate")
    text = api.client.get(regen, params={"target": "character:char_kiwilo:text"}).json()
    assert text["units"] == {"llm_calls": 1}
    portrait = api.client.get(regen, params={"target": "character:char_kiwilo:image:portrait"}).json()
    assert portrait["units"] == {"llm_calls": 0, "images": 1, "edit_images": 2, "tts_chars": 0}
    assert api.client.get(regen, params={"target": "character:char_nobody:text"}).status_code == 404
    assert api.client.get(regen, params={"target": "character:char_kiwilo:image:extra:1"}).status_code == 400
    assert api.client.get(_url(story_id, "/estimate/import")).status_code == 400
    assert api.client.get(_url(story_id, "/estimate/script"), params={"ep": 1}).status_code == 409  # phase 3: not ready


def test_the_places_estimate_counts_the_list_the_user_is_editing_not_only_the_saved_proposal(api):
    """The places board lets the user drop items from the proposal before
    creating them (ProposalEditor); the estimate chip above the "Create
    places & props" button must reflect what is on screen, not the saved
    places_proposal.json the user may have already trimmed."""
    story_id = _cast_via_api(api)
    proposal = {"$schema": "places_proposal_v1", "places": P0_REPLY["places"],
                "props": [{"name": "Le coco-téléphone", "one_line": "Il sonne.", "owner": None}], "updated_at": NOW}
    api.store.write_doc(story_id, "places_proposal.json", proposal, now=NOW)

    # The saved proposal alone (no ?place=/?prop=): 2 places + 1 prop, as before.
    saved = _estimate(api, story_id, "places")
    assert saved["units"] == {"llm_calls": 3, "images": 3, "edit_images": 0, "tts_chars": 0}

    # The user removed one place and the only prop on screen: the estimate
    # must count only what is left, not the saved proposal's three items.
    response = api.client.get(_url(story_id, "/estimate/places"),
                              params={"place": ["Le camp de plage"]})
    assert response.status_code == 200, response.text
    trimmed = response.json()
    assert trimmed["units"] == {"llm_calls": 1, "images": 1, "edit_images": 0, "tts_chars": 0}

    # The user added a place the proposal never had, and no props at all.
    response = api.client.get(_url(story_id, "/estimate/places"),
                              params={"place": ["Le camp de plage", "La grotte secrète"]})
    added = response.json()
    assert added["units"] == {"llm_calls": 2, "images": 2, "edit_images": 0, "tts_chars": 0}

    # ?prop= alone: no places at all, one prop.
    response = api.client.get(_url(story_id, "/estimate/places"),
                              params={"prop": ["Le coco-téléphone"]})
    props_only = response.json()
    assert props_only["units"] == {"llm_calls": 1, "images": 1, "edit_images": 0, "tts_chars": 0}


# ================================================================= the token

NEW_ROUTES = [
    ("POST", "/steps/cast", {"params": CAST_PARAMS}),
    ("POST", "/steps/places_proposal", {}),
    ("POST", "/approve/character:char_kiwilo", None),
    ("POST", "/approve/season", None),
    ("POST", "/regenerate", {"target": "character:char_kiwilo:text"}),
    ("PATCH", "/characters/char_kiwilo", {"name": "Kiwi"}),
    ("PATCH", f"/places/{BEACH}", {"name": "x"}),
    ("PATCH", f"/props/{PHONE}", {"name": "x"}),
    ("DELETE", "/characters/char_kiwilo", None),
    ("DELETE", f"/places/{BEACH}", None),
    ("DELETE", f"/props/{PHONE}", None),
    ("DELETE", f"/characters/char_kiwilo/uploads/{'0' * 32}.png", None),
    ("GET", "/media/characters/char_kiwilo/portrait.png", None),
    ("GET", "/estimate/cast", None),
    ("GET", "/characters/char_kiwilo/voices", None),
]


def test_every_story_route_is_under_the_routers_token(api):
    from web.api.auth import require_token

    routes = [route for route in api.routes.router.routes if hasattr(route, "dependant")]
    assert len(routes) >= 23
    for route in routes:
        assert any(dep.call is require_token for dep in route.dependant.dependencies), route.path


def test_the_new_routes_refuse_a_request_without_the_token_before_reading_it(api, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import auth

    story_id = _story(api.store)
    _new_character(api.store, story_id, "char_kiwilo", "Kiwilo")
    monkeypatch.setenv("API_TOKEN", "test-token-12345")
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    monkeypatch.setattr(auth, "_TOKEN", None)
    app = FastAPI()
    app.include_router(api.routes.router)
    before = api.store.read_entity(story_id, "characters", "char_kiwilo")
    with TestClient(app) as client:
        for method, path, body in NEW_ROUTES:
            response = client.request(method, _url(story_id, path), json=body)
            assert response.status_code == 401, (method, path)
        response = client.post(_url(story_id, "/characters/char_kiwilo/uploads"),
                               files={"file": ("a.png", b"x" * 1000, "image/png")})
        assert response.status_code == 401
        headers = {"Authorization": "Bearer test-token-12345"}
        assert client.get(_url(story_id, "/estimate/cast"), headers=headers).status_code == 200
    # Nothing was read, made or written.
    folder = api.outputs / "stories" / story_id / "characters" / "char_kiwilo"
    assert not (folder / "refs").exists()
    assert api.store.read_entity(story_id, "characters", "char_kiwilo") == before
    assert api.jobs.list_jobs() == []
    monkeypatch.setattr(auth, "_TOKEN", None)


ENTITY_ROUTES = [route for route in NEW_ROUTES if route[1].startswith(("/characters/", "/places/", "/props/",
                                                                       "/media/"))]
ENTITY_ROUTES.append(("POST", "/characters/char_kiwilo/uploads", None))


@pytest.mark.parametrize("bad_id", ["ABC", "0123456789AB", UNKNOWN_ID, "0123456789abc"])
@pytest.mark.parametrize("method,path,body", ENTITY_ROUTES)
def test_a_bad_or_unknown_story_id_is_a_404_on_every_entity_route(api, bad_id, method, path, body):
    other = _story(api.store)
    _new_character(api.store, other, "char_kiwilo", "Kiwilo")
    response = api.client.request(method, _url(bad_id, path), json=body)
    assert response.status_code == 404
    # The route's own answer, from the id check -- not the router's "Not Found".
    assert response.json()["detail"] in ("Story not found", "File not found")
    assert api.jobs.list_jobs() == [] and api.submitted == []
    assert sorted(p.name for p in (api.outputs / "stories").iterdir()) == [other]
    assert not (api.outputs / "stories" / other / "characters" / "char_kiwilo" / "refs").exists()
