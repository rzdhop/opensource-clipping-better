"""The phase-3 step runners: ``script``, ``storyboard`` and the episode targets
of ``regenerate`` (AI Story phase 3, stage 6; spec 3 steps 8-9, 4.2 rows
E1-E4/T1/T1r, 9.2).

Every step writes into a real ``StoryStore`` under ``tmp_path``, seeded with a
``ready`` story shaped like the live one (``b1104ec66b05``): French, three
characters (two leads and a recurring host), two places with a day and a
night plate, one prop, an eight-episode season arc and a locked fruit_drama
style. The LLM is a stand-in for ``llm.run_chain`` answering per prompt id
from a queue (an entry is a reply, an exception, or ``f(call)`` building a
reply from the request -- the speakers and tags a schema allows), recording
every call; two tests drive the real ``run_chain`` through a fake client
factory to prove which links are built. Offline and hermetic: no key, chain
or cap of the machine reaches a test, no request leaves the process, and the
repository's ``data/`` files and ``outputs/stories`` are fingerprinted before
and after every test.

Stdlib + pytest (the CI environment, DEC-012). The step modules are imported
inside the tests, so on the parent commit each test fails on its own instead
of the file failing to collect.
"""

from __future__ import annotations

import copy
import functools
import hashlib
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping.aistory import defaults, prompts, schemas, steps, stylelock, templates, timing
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken, Cancelled
from clipping.providers.errors import ProviderError
from clipping.providers.registry import Link

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-27T10:00:00+00:00"
LINK = Link("gemini", "gemini-test")

CHAIN_VARS = ("LLM_CHAIN", "ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD",
              "BUDGET_PROFILE", "ALLOW_SLOW_CHAIN", "MAX_QUEUED_JOBS")
REAL_FILES = tuple(ROOT / "data" / name for name in ("usage.json", "spend.json", "chain_test_ledger.json"))
REAL_STORIES = (ROOT / "outputs" / "stories", ROOT / "outputs" / "stories.json")

# Test values only: every request goes to a fake.
SETTINGS = {"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key"}

KIWILO, MANGELLA, BROCCOLIA = "char_kiwilo", "char_mangella", "char_broccolia"
PARLOIR, PISCINE = "place_le_parloir_des_secrets", "place_la_piscine_de_la_trahison"
PHONE = "prop_telephone_en_noix_de_coco"
NAMES = {KIWILO: "Kiwilo", MANGELLA: "Mangella", BROCCOLIA: "Broccolia"}
# Stage 12b: serial_60s_v1's default_body_count is 8 (both episode 1 and
# episode 2 -- timing.episode_slots), so E1_REPLY below carries 8 body
# scenes, s02..s09; the cliffhanger is s10.
BODY = ["s02", "s03", "s04", "s05", "s06", "s07", "s08", "s09"]
ALL_SCENES = ["s01"] + BODY + ["s10"]


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
    from clipping.providers import budget, limits, llm, pacing, transport

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in CHAIN_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "data" / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    limits.reset()
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()

    def no_network(method, url, **_kwargs):
        raise AssertionError(f"a real request was attempted: {method} {url}")

    monkeypatch.setattr(transport, "urllib_transport", no_network)

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


def _new():
    """The stage-6 modules (absent on the parent commit)."""
    from clipping.aistory.steps import episode_common, episode_regenerate, regenerate, script, storyboard

    return SimpleNamespace(common=episode_common, regen=episode_regenerate, regenerate=regenerate,
                           script=script, storyboard=storyboard)


# ------------------------------------------------------------ the story

def _image(name, consistency="base", seed=7):
    return {"name": name, "consistency": consistency, "source": "pollinations/flux", "seed": seed,
            "created_at": NOW}


def _character(char_id, role, descriptor, items, *, wants, fears, speech, voice_id):
    return {
        "$schema": "character_v1", "char_id": char_id, "name": NAMES[char_id], "role": role,
        "archetype": "manipulateur charmeur", "one_line": f"{NAMES[char_id]} joue pour gagner.",
        "descriptor": descriptor, "signature_items": items,
        "personality": {"traits": ["Manipulateur", "Charmeur"], "wants": wants, "fears": fears,
                        "speech_style": speech},
        "relationships": {},
        "voice": {"provider": "edge", "voice_id": voice_id, "rate": "+0%", "pitch": "+0Hz",
                  "direction": "over-acted telenovela delivery", "sample_line": "Moi, mentir ? Jamais."},
        "voice_hints": None,
        "refs": {"portrait": _image("portrait.jpg"), "turnaround": None, "expressions": None, "extra": [],
                 "uploads": []},
        "ref_seed": 7, "prompt_block": f"{descriptor}.",
        "state": {"alive": True, "location": None, "arc_notes": []},
        "source": "sketch", "approved_at": NOW, "created_at": NOW, "updated_at": NOW,
    }


CHARACTERS = [
    _character(KIWILO, "lead",
               "A fuzzy, dark brown ripe kiwi fruit serving as a human-scale head, set on a human body "
               "wearing a sharp tailored charcoal suit.",
               ["A thin gold chain around the neck", "A sharp tailored charcoal suit"],
               wants="Garder le pouvoir sur l'île.", fears="Être démasqué.", speech="Des phrases courtes et mielleuses.",
               voice_id="fr-FR-HenriNeural"),
    _character(MANGELLA, "lead",
               "A smooth, deep red ripe mango serving as a human-scale head, set atop an elegant human body "
               "wearing a tailored emerald green pantsuit.",
               ["sparkling rhinestone crown hair clip", "gold statement necklace"],
               wants="Écraser toute concurrence.", fears="Perdre le contrôle.", speech="Un ton glacial, condescendant.",
               voice_id="fr-FR-DeniseNeural"),
    _character(BROCCOLIA, "recurring",
               "A dense, dark green cluster of tight broccoli florets serving as a human-scale head, set on a "
               "human body wearing an elegant emerald velvet evening gown.",
               ["Rhombus crystal rhinestone headband microphone", "Tailored emerald velvet evening gown"],
               wants="Contrôler chaque élimination.", fears="Voir ses secrets révélés.", speech="Solennelle, théâtrale.",
               voice_id="fr-FR-VivienneMultilingualNeural"),
]


def _place(place_id, name, descriptor, layout):
    return {
        "$schema": "place_v1", "place_id": place_id, "name": name, "one_line": f"{name}, sur l'île.",
        "descriptor": descriptor, "layout_notes": layout,
        "time_variants": {"day": _image("variant_day.jpg"), "night": _image("variant_night.jpg", "prompt_only")},
        "prompt_block": f"{descriptor}.", "approved_at": NOW, "created_at": NOW, "updated_at": NOW,
    }


PLACES = [
    _place(PARLOIR, "Le Parloir des Secrets",
           "A dimly lit tropical wooden confession booth with a carved bamboo chair",
           "A rustic wooden stool sits center. A hidden-camera slit is cut into the right wall."),
    _place(PISCINE, "La Piscine de la Trahison",
           "A luxurious turquoise swimming pool surrounded by white wooden loungers",
           "Crystal-clear water fills the foreground. A tiki bar stands to the right."),
]

PROP = {
    "$schema": "prop_v1", "prop_id": PHONE, "name": "Téléphone en noix de coco",
    "one_line": "L'appareil qui annonce les éliminations.",
    "descriptor": "A polished half coconut shell shaped like a vintage telephone with glowing flower buttons",
    "owner_char_id": None, "image": _image("image.jpg"), "prompt_block": "A polished half coconut shell.",
    "approved_at": NOW, "created_at": NOW, "updated_at": NOW,
}

ARC_FUNCTIONS = ["setup", "escalation", "complication", "midpoint_twist", "crisis", "climax_and_reset",
                 "crisis", "climax_and_reset"]


def _season(recaps=None, relationships=None):
    return {
        "$schema": "season_arc_v1", "episodes_planned": 8,
        "arc": [{"ep": ep, "function": ARC_FUNCTIONS[ep - 1],
                 "summary": f"Épisode {ep} : les alliances de l'île tremblent encore.",
                 "open_hooks_in": [] if ep == 1 else ["Qui a volé le téléphone ?"],
                 "open_hooks_out": ["Kiwilo va-t-il trahir Mangella ?"], "characters": [KIWILO, MANGELLA]}
                for ep in range(1, 9)],
        "series_memory": {"recaps": dict(recaps or {}), "open_hooks": [],
                          "relationship_state": dict(relationships or {}), "introduced": {}},
        "audience_feedback": [], "approved_at": NOW, "updated_at": NOW,
    }


def _lock():
    draft = stylelock.build_style_lock(templates.load_style("fruit_drama"), {}, now=NOW)
    return stylelock.lock_style(draft, now=NOW)


def _ready_story(store, *, recaps=None, relationships=None, with_prop=True, v2=False, knowledge=True):
    """A French Tentafruit story whose derived status is ``ready`` (with no
    prop at all when *with_prop* is False). *v2* (phase 7 stage 3c, A11)
    puts the story on the v2 pipeline (``media_policy.is_v2``), the only
    thing E1's ``new_objects`` and the storyboard's prop-image gate read.

    Phase 7 stage 5b (DEC-228), re-pinned on purpose: a v2 story's script is
    written only from an approved, current knowledge base (the episode
    gate), so a v2 story here gets one (:func:`_approved_knowledge`) unless
    *knowledge* is False; a legacy story never has one."""
    story_id = store.create(language="fr", seed_text=None, style_template_id="fruit_drama", now=NOW)["story_id"]
    concept = templates.localize_concept(
        next(c for c in templates.load_concepts() if c["concept_id"] == "tentafruit_island"), "fr")

    def setup(doc):
        doc["concept_id"] = "tentafruit_island"
        doc["concept"] = concept
        doc["title"] = concept["title"]
        doc["logline"] = concept["logline"]
        doc["premise"] = "Chaque semaine, un couple est éliminé. Le téléphone en noix de coco annonce le vote."
        doc["tone"] = "Sombre, cynique, satirique"
        doc["generation_profile"]["consistency_mode"] = "prompt_only"
        if v2:
            doc["generation_profile"]["pipeline"] = defaults.PIPELINE_V2
        for key in ("concept", "bible", "style"):
            doc["approvals"][key] = NOW

    store.update(story_id, setup, now=NOW)
    store.write_doc(story_id, "style_lock.json", _lock(), now=NOW, validator=schemas.style_lock_errors)
    for doc in CHARACTERS:
        store.write_entity(story_id, "characters", copy.deepcopy(doc), now=NOW)
    for doc in PLACES:
        store.write_entity(story_id, "places", copy.deepcopy(doc), now=NOW)
    if with_prop:
        store.write_entity(story_id, "props", copy.deepcopy(PROP), now=NOW)
    store.write_doc(story_id, "season.json", _season(recaps, relationships), now=NOW)
    store.update(story_id, lambda doc: doc["approvals"].update(season=NOW), now=NOW)
    if v2 and knowledge:
        store.write_knowledge(story_id, _approved_knowledge(), now=NOW)
    assert store.get(story_id)["status"] == "ready"
    return story_id


def _approved_knowledge():
    """A minimal knowledge base with its four sections, approved at its
    revision (phase 7 stage 5b): what the v2 episode gate needs."""
    return {
        "$schema": "story_knowledge_v1", "rev": 1, "approved_at": NOW, "approved_rev": 1, "updated_at": NOW,
        "world": {"geography": "Le parloir domine la piscine.", "period_details": "Une téléréalité.",
                  "visual_motifs": ["des noix de coco fêlées"]},
        "timeline": [{"ep": 1, "beats": [{"what": "Le téléphone sonne au parloir.", "place_id": PARLOIR,
                                          "who": [KIWILO], "objects": [], "knows_after": {}}]}],
        "props_registry": [],
        "ledger_seed": {},
    }


def _story_bytes(store, story_id):
    return (Path(store.story_dir(story_id)) / "story.json").read_bytes()


# ------------------------------------------------------------ the fakes

class Log(list):
    def __call__(self, line):
        self.append(str(line))


class Clock:
    """A monotonic clock a fake call advances."""

    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


_V2_TWINS = {"E1v2": "E1", "E2v2": "E2", "E3v2": "E3"}


class FakeLLM:
    """Stands in for ``llm.run_chain``: answers each prompt id from its own
    queue (or, with ``default``, a builder for every call of that id),
    records every call as a dict of its kwargs plus ``prompt``. An entry is a
    reply, an exception to raise, or ``f(call)`` returning either. *clock*
    and *advance*: each call moves the clock on."""

    def __init__(self, *, clock=None, advance=0.0, default=None, **queues):
        self.queues = {prompt: list(replies) for prompt, replies in queues.items()}
        self.default = dict(default or {})
        self.calls = []
        self.clock = clock
        self.advance = advance
        self._ids = {name: prompt for prompt, name in prompts.SCHEMA_NAMES.items()}

    def __call__(self, chain, **kwargs):
        prompt = self._ids[kwargs["schema_name"]]
        call = dict(kwargs, chain=list(chain), prompt=prompt)
        self.calls.append(call)
        if self.clock is not None:
            self.clock.now += self.advance
        # Phase 7 stage 5c (DEC-228), re-pinned on purpose: a v2 story's script calls E1v2/E2v2/E3v2 (own ids,
        # own schema names); with nothing queued under that id, they answer from their v1 twin's queue -- the
        # replies have the same shape -- so a fixture written for either pipeline serves both.
        source = prompt
        if not self.queues.get(prompt) and prompt not in self.default and prompt in _V2_TWINS:
            source = _V2_TWINS[prompt]
        queue = self.queues.get(source) or []
        if queue:
            reply = queue.pop(0)
        elif source in self.default:
            reply = self.default[source]
        else:
            raise AssertionError(f"no {prompt} reply queued (call {len(self.calls)})")
        if callable(reply) and not isinstance(reply, BaseException):
            reply = reply(call)
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply), LINK

    def prompts(self):
        return [call["prompt"] for call in self.calls]

    def of(self, prompt):
        return [call for call in self.calls if call["prompt"] == prompt]


def _stub(function, place, variant, characters, props, summary, emotion, target):
    return {"function": function, "place_id": place, "time_variant": variant, "characters": characters,
            "props": props, "summary": summary, "emotion": emotion, "target_duration_s": target}


E1_REPLY = {
    "title": "Le coco sonne deux fois",
    "scenes": [
        _stub("hook", PARLOIR, "day", [KIWILO, MANGELLA], [PHONE], "Le téléphone en noix de coco sonne au parloir.",
              "shocked", 2.5),
        _stub("setup", PARLOIR, "day", [KIWILO, MANGELLA], [], "Kiwilo propose à Mangella une alliance secrète.",
              "scheming", 6.0),
        _stub("rising", PISCINE, "day", [MANGELLA, BROCCOLIA], [], "Broccolia interroge Mangella au bord de l'eau.",
              "tension", 6.0),
        _stub("peak", PISCINE, "day", [KIWILO, MANGELLA, BROCCOLIA], [PHONE],
              "Le téléphone annonce un vote surprise.", "shocked", 7.0),
        _stub("turn", PARLOIR, "night", [KIWILO], [], "Kiwilo avoue son plan à la caméra cachée.", "scheming", 5.0),
        _stub("rising", PISCINE, "night", [MANGELLA, KIWILO], [], "Mangella découvre le mensonge de Kiwilo.",
              "angry", 6.0),
        _stub("turn", PISCINE, "night", [BROCCOLIA, MANGELLA], [], "Broccolia offre un marché à Mangella.",
              "tension", 5.0),
        # s08, s09 (stage 12b: default_body_count 8, up from the old 6):
        # Kiwilo alone, like s05, so the measure-test suite's per-character
        # line counts for Mangella and Broccolia are untouched by the extra
        # scenes -- only Kiwilo's and the totals grow.
        _stub("setup", PISCINE, "day", [KIWILO], [], "Kiwilo tente de reprendre le contrôle du vote.",
              "tension", 5.0),
        _stub("peak", PARLOIR, "night", [KIWILO], [PHONE], "Kiwilo hésite, la main sur le téléphone.",
              "scheming", 7.0),
        _stub("cliffhanger", PARLOIR, "night", [KIWILO, MANGELLA, BROCCOLIA], [PHONE],
              "Le téléphone désigne Kiwilo.", "shocked", 3.0),
    ],
}


def _speakers(call):
    return call["schema"]["properties"]["lines"]["items"]["properties"]["speaker"]["enum"]


def e2_reply(call, *, text="Tu crois vraiment que je vais te suivre ?"):
    speakers = _speakers(call)
    return {
        "lines": [
            {"speaker": speakers[0], "text": text, "emotion": "tension", "delivery": "low and sharp"},
            {"speaker": speakers[-1], "text": "Tu n'as pas le choix, chérie.", "emotion": "scheming",
             "delivery": "smug whisper"},
        ],
        "sfx_cues": [{"at": "start", "cue": "dramatic_sting"}, {"at": "2", "cue": "gasp_crowd"}],
        "on_screen_text": None,
    }


def e2_wrong_speaker(call):
    """A reply whose speaker is not one the scene allows."""
    reply = e2_reply(call)
    others = [cid for cid in NAMES if cid not in _speakers(call)]
    reply["lines"][0]["speaker"] = others[0]
    return reply


def e2_short_reply(call):
    """A reply with a single one-word line: under half of any word budget
    the fixture's scenes ever get (spec 4.2, F3's floor)."""
    speakers = _speakers(call)
    return {
        "lines": [{"speaker": speakers[0], "text": "Non.", "emotion": "tension", "delivery": "flat"}],
        "sfx_cues": [], "on_screen_text": None,
    }


def e2_short_wrong_speaker(call):
    """A reply that is both under the word floor *and* has a speaker the
    scene does not allow -- a real problem, never forgiven by the floor's
    own leniency."""
    reply = e2_short_reply(call)
    others = [cid for cid in NAMES if cid not in _speakers(call)]
    reply["lines"][0]["speaker"] = others[0]
    return reply


def e2_long_reply(call):
    """Two lines at the 22-word cap (44 words total): well over 1.5x any
    word budget a fixture scene ever gets (spec 4.2, F3 round 2)."""
    speakers = _speakers(call)

    def words(i):
        return " ".join(f"w{i}_{j}" for j in range(22))

    return {
        "lines": [
            {"speaker": speakers[0], "text": words(0), "emotion": "tension", "delivery": "fast"},
            {"speaker": speakers[-1], "text": words(1), "emotion": "tension", "delivery": "fast"},
        ],
        "sfx_cues": [], "on_screen_text": None,
    }


HOOK_PART = {"lines": [{"speaker": KIWILO, "text": "Ce soir, quelqu'un quitte l'île.", "emotion": "shocked",
                        "delivery": "breathless"}],
             "on_screen_text": "Vote surprise ce soir"}
CLIFF_PART = {"reveal": "Le téléphone affiche le nom de Kiwilo.",
              "lines": [{"speaker": MANGELLA, "text": "C'est toi, Kiwilo.", "emotion": "shocked", "delivery": "cold"}]}
TEASER = "Demain, Kiwilo joue sa dernière carte."
E3_FULL = {"hook": HOOK_PART, "cliffhanger": CLIFF_PART, "teaser": TEASER}
E4_PASSED = {"passed": True, "issues": []}
E4_ISSUES = {"passed": False, "issues": [
    {"scene_id": "s03", "kind": "character", "fix": "Broccolia parle trop gentiment ici."},
    {"scene_id": None, "kind": "continuity", "fix": "Le vote surprise n'est jamais expliqué."},
]}


# Phase 7 stage 6a (DEC-230/231): a v2 story's script step judges the script
# (J1) after E4, refuses an E2v2 reply repeating a line of the episode, and --
# while its estimate is under the window -- rewrites up to 2 of its shortest
# scenes (the fill pass).
J1_PASSED = {"who_wants_what": "Kiwilo veut garder le pouvoir sur l'île.",
             "what_happens": "Le téléphone annonce un vote surprise et désigne Kiwilo.",
             "why_it_matters": "Le perdant du vote quitte l'île.", "passed": True, "issues": []}
J1_ISSUES = dict(J1_PASSED, passed=False, issues=[
    {"scene_id": "s05", "kind": "unmotivated", "fix": "Montrez pourquoi Kiwilo avoue son plan."}])


def e2_v2_reply(call, *, short=False):
    """A v2 story's E2 reply: lines of the scene's own (a tag drawn from its
    stub line) -- a v2 reply may not repeat a line of the episode -- a little
    longer than :func:`e2_reply`'s, so the fixture's episode lands inside its
    window (65.6 s of 55-80 s); *short* lands it under (42.4 s)."""
    stub = call["user"].split("Scene (", 1)[1].split("\n", 1)[0]
    tag = hashlib.sha256(stub.encode("utf-8")).hexdigest()[:6]
    speakers = _speakers(call)
    if short:
        first, second = f"Non {tag[:3]}a, si {tag[:3]}b, ça va.", f"Bon, {tag[:3]}c, oh {tag[:3]}d, va."
    else:
        first, second = f"Écoute bien alpha{tag} beta{tag} gamma{tag} ce soir.", f"Jamais epsilon{tag} zeta{tag}, chérie."
    return {
        "lines": [{"speaker": speakers[0], "text": first, "emotion": "tension", "delivery": "low and sharp"},
                  {"speaker": speakers[-1], "text": second, "emotion": "scheming", "delivery": "smug whisper"}],
        "sfx_cues": [{"at": "start", "cue": "dramatic_sting"}, {"at": "2", "cue": "gasp_crowd"}],
        "on_screen_text": None,
    }


def _script_llm(*, v2=False, **overrides):
    """The script step's replies; *v2* (phase 7 stage 6a, re-pinned on
    purpose): E2 replies of each scene's own (:func:`e2_v2_reply`, two more
    for the fill pass) and J1 answering :data:`J1_PASSED` by default."""
    e2 = [e2_v2_reply] * (len(BODY) + 2) if v2 else [e2_reply] * len(BODY)
    queues = {"E1": [E1_REPLY], "E2": e2, "E3": [E3_FULL], "E4": [E4_ISSUES]}
    queues.update(overrides)
    return FakeLLM(default={"J1": J1_PASSED} if v2 else None, **queues)


def _numbered_lines(user):
    block = user.split("Numbered lines:\n", 1)[1].split("\n\n", 1)[0]
    return len(re.findall(r"^\d+\. ", block, flags=re.M))


def t1_reply(call):
    tags = call["schema"]["properties"]["shots"]["items"]["properties"]["subjects"]["items"]["enum"]
    place = next(tag for tag in tags if tag.startswith("#"))
    chars = [tag for tag in tags if tag.startswith("@")]
    who = chars or [place]
    n = _numbered_lines(call["user"])
    return {"shots": [
        {"framing": "wide_establishing", "camera_motion": "pan_lr", "modifiers": [],
         "action": f"Wide view of {place}.", "subjects": [place], "lines": []},
        {"framing": "medium_two_shot" if len(chars) > 1 else "medium_single", "camera_motion": "push_in",
         "modifiers": [], "action": " and ".join(who) + " face each other.", "subjects": who,
         "lines": list(range(1, n + 1))},
    ]}


def t1r_reply(call):
    """The replaced shot's own framing and lines, a new action."""
    tags = call["schema"]["properties"]["shot"]["properties"]["subjects"]["items"]["enum"]
    who = next((tag for tag in tags if tag.startswith("@")), None) or next(tag for tag in tags if tag.startswith("#"))
    replaced = re.search(r"- shot \d+ <- replace this one: ([a-z_]+) / ([a-z_]+), lines (.*)$", call["user"], re.M)
    lines = [] if replaced.group(3) == "none" else json.loads(replaced.group(3))
    return {"shot": {"framing": replaced.group(1), "camera_motion": replaced.group(2), "modifiers": [],
                     "action": f"{who} leans in, whispering a secret.", "subjects": [who], "lines": lines}}


def _ctx(store, story_id, *, step="script", ep=1, params=None, settings=None):
    log = Log()
    ctx = steps.StepContext(
        job_id="job000000001", story_id=story_id, step=step, ep=ep, params=params or {},
        cancel=CancelToken(), settings_env=dict(SETTINGS if settings is None else settings),
        outputs_dir=store.outputs_dir, on_log=log,
    )
    return ctx, log


def _run(module, store, story_id, *, llm, step="script", ep=1, params=None, settings=None, clock=None):
    ctx, log = _ctx(store, story_id, step=step, ep=ep, params=params, settings=settings)
    clock = clock or Clock(100.0)
    return module.run(ctx, runner=llm, time_fn=clock), log


def _failed(module, store, story_id, *, llm, step="script", ep=1, params=None, settings=None, clock=None):
    ctx, log = _ctx(store, story_id, step=step, ep=ep, params=params, settings=settings)
    clock = clock or Clock(100.0)
    with pytest.raises(steps.StepFailed) as caught:
        module.run(ctx, runner=llm, time_fn=clock)
    return str(caught.value), log


def _regenerate(store, story_id, target, *, llm, note=None):
    m = _new()
    return _run(m.regenerate, store, story_id, llm=llm, step="regenerate", ep=None,
                params={"target": target, "note": note})


def _script(store, story_id, ep=1):
    return store.read_episode_doc(story_id, ep, "script.json")


def _storyboard(store, story_id, ep=1):
    return store.read_episode_doc(story_id, ep, "storyboard.json")


def _context_errors(store, story_id, script):
    lock = store.read_doc(story_id, "style_lock.json")
    places = {doc["place_id"]: list(doc["time_variants"]) for doc in PLACES}
    return schemas.episode_script_context_errors(
        script, cast_ids=list(NAMES), places=places, prop_ids=[PHONE], sfx_cues=lock["audio"]["sfx_cues"],
        narrator_enabled=False, max_places=lock["episode_defaults"]["max_places"])


def _written_script(store):
    """A ready story with its episode 1 fully written (E4 found two issues)."""
    m = _new()
    story_id = _ready_story(store)
    _run(m.script, store, story_id, llm=_script_llm())
    return story_id


def _scene(script, sid):
    return next(scene for scene in script["scenes"] if scene["scene_id"] == sid)


# ================================================================== script

def test_a_full_script_is_one_e1_one_e2_per_body_scene_one_e3_and_one_e4(store):
    m = _new()
    story_id = _ready_story(store)
    before = _story_bytes(store, story_id)
    llm = _script_llm()

    summary, log = _run(m.script, store, story_id, llm=llm)

    assert llm.prompts() == ["E1"] + ["E2"] * len(BODY) + ["E3", "E4"]
    script = _script(store, story_id)
    assert schemas.episode_script_errors(script) == []
    assert _context_errors(store, story_id, script) == []
    assert [scene["scene_id"] for scene in script["scenes"]] == ALL_SCENES
    assert all(scene["state"] == "written" for scene in script["scenes"])
    assert [scene["source"] for scene in script["scenes"]] == ["E3"] + ["E2"] * len(BODY) + ["E3"]
    for scene in script["scenes"]:
        assert [line["line_id"] for line in scene["lines"]] == [
            schemas.line_id_for(scene["scene_id"], k) for k in range(len(scene["lines"]))]
        assert all(line["timing"]["source"] == "estimated" for line in scene["lines"])
    s02 = _scene(script, "s02")
    assert [line["line_id"] for line in s02["lines"]] == ["l08", "l09"]
    assert s02["sfx_cues"] == [{"at": "start", "cue": "dramatic_sting"}, {"at": "l09", "cue": "gasp_crowd"}]
    # F3: E1_REPLY's own targets sum to 52.5 s, under serial_60s_v1's target_s
    # (60): script.apply_e1's normalisation raises every scene toward its
    # slot's high end proportionally to its own room, so s02 (setup, 6.0 of
    # its 4-8s range, 2.0 s of room out of 20.0 s total room, scale 7.5/20 =
    # 0.375) lands at 6.0 + 2.0 * 0.375 = 6.75.
    assert s02["target_duration_s"] == 6.75 and s02["rev"] == 1
    assert script["title"] == E1_REPLY["title"]
    assert script["hook"] == {"on_screen_text": "Vote surprise ce soir"}
    assert _scene(script, "s01")["lines"][0]["line_id"] == "l04"
    assert script["cliffhanger"] == {"scene_id": "s10", "reveal": CLIFF_PART["reveal"], "cut_to_black": False}
    assert [line["line_id"] for line in _scene(script, "s10")["lines"]] == ["l40"]
    assert script["next_episode_teaser"] == TEASER
    report = script["consistency_report"]
    assert report["passed"] is False and report["checked_rev"] == script["rev"] and report["stale"] is False
    assert report["issues"] == E4_ISSUES["issues"]
    assert script["timing"] == timing.episode_timing(script, templates.load_episode_template("serial_60s_v1"),
                                                     "fr", style_lock=store.read_doc(story_id, "style_lock.json"))
    assert script["approved_at"] is None and script["created_at"] and script["rev"] == 1
    assert summary["ep"] == 1 and summary["scenes"] == len(ALL_SCENES)
    # The house style's progress lines.
    assert "🎬 Episode 1: beat sheet (E1)" in log
    assert "📝 Scene 2 of 10 (s02, setup)" in log
    assert any(line.startswith("⏱ ") and "estimated" in line for line in log)
    assert "🔍 Consistency: 2 issues" in log
    assert sum(line.startswith("✍️ E2 via gemini/gemini-test") for line in log) == len(BODY)
    # E2 was given its word budget, the outline and the previous scene's last line.
    second = llm.of("E2")[1]["user"]
    assert "Episode outline:" in second and "Previous scene: Kiwilo propose" in second
    assert "Its last line -- Mangella: Tu n'as pas le choix, chérie." in second
    assert "words of dialogue in total: not fewer than" in second
    # RC-E2: the story itself is never touched.
    assert _story_bytes(store, story_id) == before


def test_the_script_is_written_after_every_accepted_call(store):
    m = _new()
    story_id = _ready_story(store)
    seen = []

    def spy(prompt, reply):
        def answer(call):
            seen.append((prompt, _script(store, story_id)))
            return reply(call) if callable(reply) else reply
        return answer

    llm = FakeLLM(E1=[spy("E1", E1_REPLY)], E2=[spy("E2", e2_reply) for _ in BODY], E3=[spy("E3", E3_FULL)],
                  E4=[spy("E4", E4_PASSED)])
    _run(m.script, store, story_id, llm=llm)

    assert seen[0] == ("E1", None)
    # Before each E2, every body scene before it is already on disk.
    for index, (prompt, doc) in enumerate(seen[1:1 + len(BODY)]):
        assert prompt == "E2"
        written = [s["scene_id"] for s in doc["scenes"] if s["state"] == "written"]
        assert written == BODY[:index]
    assert seen[-2][0] == "E3"
    assert [s["scene_id"] for s in seen[-2][1]["scenes"] if s["state"] == "written"] == BODY
    assert seen[-1][1]["next_episode_teaser"] == TEASER and seen[-1][1]["consistency_report"] is None


def test_a_failed_e2_keeps_every_other_scene_and_a_rerun_writes_only_that_scene(store):
    m = _new()
    story_id = _ready_story(store)
    before = _story_bytes(store, story_id)
    queue = [e2_reply, e2_reply, ProviderError("every provider failed", [("gemini/gemini-test", "HTTP 503")]),
             e2_reply, e2_reply, e2_reply, e2_reply, e2_reply]
    llm = _script_llm(E2=queue)

    message, log = _failed(m.script, store, story_id, llm=llm)

    assert llm.prompts() == ["E1"] + ["E2"] * len(BODY) + ["E3"]
    assert "scene:1:s04" in message and "HTTP 503" in message
    assert "✖ Scene s04 failed" in "\n".join(log)
    script = _script(store, story_id)
    assert schemas.episode_script_errors(script) == []
    assert _scene(script, "s04")["state"] == "stub" and _scene(script, "s04")["lines"] == []
    assert all(_scene(script, sid)["state"] == "written" for sid in ALL_SCENES if sid != "s04")
    assert script["consistency_report"] is None  # E4 checks a complete script only

    again = _script_llm(E1=[], E3=[], E4=[E4_PASSED])
    _run(m.script, store, story_id, llm=again)

    assert again.prompts() == ["E2", "E4"]
    assert "Scene (peak, emotion: shocked): Le téléphone annonce un vote surprise." in again.of("E2")[0]["user"]
    script = _script(store, story_id)
    assert all(scene["state"] == "written" for scene in script["scenes"])
    assert script["consistency_report"]["passed"] is True
    assert _story_bytes(store, story_id) == before


def test_a_complete_rerun_makes_no_call(store):
    m = _new()
    story_id = _written_script(store)
    first = _script(store, story_id)
    llm = FakeLLM()

    _run(m.script, store, story_id, llm=llm)

    assert llm.calls == []
    second = _script(store, story_id)
    assert {k: v for k, v in second.items() if k != "updated_at"} == {
        k: v for k, v in first.items() if k != "updated_at"}


def test_a_stale_report_is_checked_again_and_nothing_else_is_asked(store):
    m = _new()
    story_id = _written_script(store)
    script = _script(store, story_id)
    script["consistency_report"]["stale"] = True
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    llm = FakeLLM(E4=[E4_PASSED])

    _run(m.script, store, story_id, llm=llm)

    assert llm.prompts() == ["E4"]
    report = _script(store, story_id)["consistency_report"]
    assert report == {"passed": True, "issues": [], "checked_rev": 1, "checked_at": report["checked_at"],
                      "stale": False}


def test_a_report_of_an_older_revision_is_checked_again(store):
    m = _new()
    story_id = _written_script(store)
    script = _script(store, story_id)
    script["rev"] = 2
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    llm = FakeLLM(E4=[E4_PASSED])

    _run(m.script, store, story_id, llm=llm)

    assert llm.prompts() == ["E4"]
    assert _script(store, story_id)["consistency_report"]["checked_rev"] == 2


def test_the_step_budget_refuses_a_call_that_could_not_finish_and_a_rerun_completes(store):
    m = _new()
    story_id = _ready_story(store)
    clock = Clock(0.0)
    llm = _script_llm(E3=[], E4=[])
    llm.clock, llm.advance = clock, 300.0

    message, log = _failed(m.script, store, story_id, llm=llm, clock=clock)

    # Calls start at 0, 300, ..., 1500 (1500 + 300 = 1800 fits); the 7th would
    # start at 1800 and could not finish inside 1800 s: it is never started.
    assert llm.prompts() == ["E1"] + ["E2"] * 5
    assert "30-minute" in message and "run the step again to continue" in message
    assert "s07" in message and "consistency check" in message
    script = _script(store, story_id)
    assert schemas.episode_script_errors(script) == []
    assert [s["scene_id"] for s in script["scenes"] if s["state"] == "written"] == BODY[:5]

    clock2 = Clock(0.0)
    # 3 scenes are still stubs (BODY[5:] = s07, s08, s09 -- 8 body scenes
    # total, stage 12b), not just s07: one E2 reply per remaining scene.
    again = FakeLLM(E2=[e2_reply, e2_reply, e2_reply], E3=[E3_FULL], E4=[E4_PASSED], clock=clock2, advance=300.0)
    _run(m.script, store, story_id, llm=again, clock=clock2)

    assert again.prompts() == ["E2", "E2", "E2", "E3", "E4"]
    assert all(scene["state"] == "written" for scene in _script(store, story_id)["scenes"])


def test_the_budget_counts_from_the_start_of_the_step(store):
    m = _new()
    Budget = m.common.Budget
    clock = Clock(50.0)
    budget = Budget(clock)
    clock.now = 50.0 + m.common.EPISODE_STEP_BUDGET_SECONDS - 300
    budget.before_call(lambda: "the consistency check")  # exactly fits
    clock.now += 0.001
    with pytest.raises(steps.StepFailed) as caught:
        budget.before_call(lambda: "the consistency check")
    assert "Left: the consistency check." in str(caught.value)


def test_a_cancel_between_calls_leaves_a_valid_partial_script(store):
    m = _new()
    story_id = _ready_story(store)
    ctx, log = _ctx(store, story_id)

    def cancel_after(call):
        ctx.cancel.cancel()
        return e2_reply(call)

    llm = FakeLLM(E1=[E1_REPLY], E2=[e2_reply, cancel_after])
    with pytest.raises(Cancelled):
        m.script.run(ctx, runner=llm, time_fn=Clock(100.0))

    assert llm.prompts() == ["E1", "E2", "E2"]
    script = _script(store, story_id)
    assert schemas.episode_script_errors(script) == []
    assert [s["scene_id"] for s in script["scenes"] if s["state"] == "written"] == ["s02", "s03"]


def test_an_e2_speaker_outside_the_scene_is_asked_again_once_then_fails_locally(store):
    m = _new()
    story_id = _ready_story(store)
    # s05 has Kiwilo alone; both replies make Mangella speak. s02-s04 succeed,
    # s05 fails twice (left a stub), s06-s09 (stage 12b: 8 body scenes) succeed.
    queue = [e2_reply, e2_reply, e2_reply, e2_wrong_speaker, e2_wrong_speaker,
             e2_reply, e2_reply, e2_reply, e2_reply]
    llm = _script_llm(E2=queue)

    message, log = _failed(m.script, store, story_id, llm=llm)

    assert llm.prompts() == ["E1"] + ["E2"] * 9 + ["E3"]
    assert _speakers(llm.of("E2")[3]) == [KIWILO]
    assert any(line.startswith("⚠️ E2 reply rejected") for line in log)
    assert "scene:1:s05" in message and "failed validation twice" in message
    script = _script(store, story_id)
    assert _scene(script, "s05")["state"] == "stub"
    assert _scene(script, "s06")["state"] == "written"


def test_an_e2_reply_under_the_word_floor_is_retried_then_accepted_with_a_log_line(store):
    """spec 4.2, F3: a reply under half the word budget is retryable, but a
    retry that is *still* only that short must not fail the whole scene (or
    the step) -- the second reply is accepted, with a log line saying so."""
    m = _new()
    story_id = _ready_story(store)
    # s02 (the first body scene) gets two short replies in a row, then the
    # remaining 7 body scenes (len(BODY) - 1) get one good reply each.
    queue = [e2_short_reply, e2_short_reply] + [e2_reply] * (len(BODY) - 1)
    llm = _script_llm(E2=queue)

    summary, log = _run(m.script, store, story_id, llm=llm)

    # s02 alone makes two E2 calls (the retry); every other scene, one.
    assert llm.prompts() == ["E1"] + ["E2"] * (len(BODY) + 1) + ["E3", "E4"]
    assert any(line.startswith("⚠️ E2 reply rejected") for line in log)
    assert any("Scene s02" in line and "accepting a reply after a retry" in line for line in log)
    script = _script(store, story_id)
    assert _scene(script, "s02")["state"] == "written"
    assert _scene(script, "s02")["lines"][0]["text"] == "Non."
    # The step did not fail overall: E4 still ran over a complete script.
    assert summary["ep"] == 1 and summary["scenes"] == len(ALL_SCENES)


def test_an_e2_reply_short_and_otherwise_broken_is_never_forgiven(store):
    """The word floor's leniency only ever forgives the floor error alone: a
    reply that is both short *and* has a disallowed speaker still fails the
    scene after its retry, exactly as a non-short broken reply would."""
    m = _new()
    story_id = _ready_story(store)
    queue = [e2_short_wrong_speaker, e2_short_wrong_speaker] + [e2_reply] * (len(BODY) - 1)
    llm = _script_llm(E2=queue)

    message, log = _failed(m.script, store, story_id, llm=llm)

    assert "scene:1:s02" in message and "failed validation twice" in message
    assert not any("accepting a reply after a retry" in line for line in log)
    script = _script(store, story_id)
    assert _scene(script, "s02")["state"] == "stub"


def test_an_e2_reply_over_the_word_ceiling_is_retried_then_accepted_with_a_log_line(store):
    """F3 round 2: the same leniency, for the opposite miss -- a reply well
    over 1.5x the word budget is retryable, and a retry that is still over
    is accepted with a log line rather than failing the scene."""
    m = _new()
    story_id = _ready_story(store)
    queue = [e2_long_reply, e2_long_reply] + [e2_reply] * (len(BODY) - 1)
    llm = _script_llm(E2=queue)

    summary, log = _run(m.script, store, story_id, llm=llm)

    assert llm.prompts() == ["E1"] + ["E2"] * (len(BODY) + 1) + ["E3", "E4"]
    assert any(line.startswith("⚠️ E2 reply rejected") for line in log)
    assert any("Scene s02" in line and "accepting a reply after a retry" in line for line in log)
    script = _script(store, story_id)
    assert _scene(script, "s02")["state"] == "written"
    assert len(_scene(script, "s02")["lines"][0]["text"].split()) == 22
    assert summary["ep"] == 1 and summary["scenes"] == len(ALL_SCENES)


def test_a_body_scene_nobody_can_speak_in_is_written_silent_without_a_call(store):
    m = _new()
    story_id = _ready_story(store)
    e1 = copy.deepcopy(E1_REPLY)
    e1["scenes"][4]["characters"] = []  # s05: nobody there, and no narrator
    llm = _script_llm(E1=[e1], E2=[e2_reply] * (len(BODY) - 1))

    _, log = _run(m.script, store, story_id, llm=llm)

    assert llm.prompts() == ["E1"] + ["E2"] * (len(BODY) - 1) + ["E3", "E4"]
    s05 = _scene(_script(store, story_id), "s05")
    assert s05["state"] == "written" and s05["lines"] == [] and s05["source"] == "E2"
    assert any("s05" in line and "no one can speak" in line for line in log)


# ---------------------------------------------- F3: episode target normalisation

def test_normalize_episode_targets_raises_low_targets_toward_the_template_target(store):
    """Live episode 1's own numbers (2026-09-27 free-tier bench): 8 body
    scenes plus a hook and a cliffhanger, E1's own per-scene clamp already
    applied, summing to 52.5 s -- under serial_60s_v1's own target_s (60).
    Every scene rises toward its own slot's high end, proportionally to the
    room each one has, until the sum reaches 60."""
    m = _new()
    ec = SimpleNamespace(template=templates.load_episode_template("serial_60s_v1"), style_lock=None)
    scenes = [
        {"function": "hook", "target_duration_s": 2.5}, {"function": "setup", "target_duration_s": 6.0},
        {"function": "rising", "target_duration_s": 6.0}, {"function": "peak", "target_duration_s": 7.0},
        {"function": "turn", "target_duration_s": 5.0}, {"function": "rising", "target_duration_s": 6.0},
        {"function": "turn", "target_duration_s": 5.0}, {"function": "setup", "target_duration_s": 5.0},
        {"function": "peak", "target_duration_s": 7.0}, {"function": "cliffhanger", "target_duration_s": 3.0},
    ]

    before, after = m.script._normalize_episode_targets(ec, scenes)

    assert before == 52.5 and after == 60.0
    assert [scene["target_duration_s"] for scene in scenes] == [
        2.875, 6.75, 6.75, 7.375, 6.125, 6.75, 6.125, 6.125, 7.375, 3.75]


def test_normalize_episode_targets_leaves_an_already_high_episode_untouched(store):
    m = _new()
    ec = SimpleNamespace(template=templates.load_episode_template("serial_60s_v1"), style_lock=None)
    scenes = [{"function": "hook", "target_duration_s": 3.5}] + \
             [{"function": "setup", "target_duration_s": 8.0} for _ in range(8)] + \
             [{"function": "cliffhanger", "target_duration_s": 5.0}]
    originals = [dict(scene) for scene in scenes]

    before, after = m.script._normalize_episode_targets(ec, scenes)

    assert before == after == sum(scene["target_duration_s"] for scene in originals)
    assert scenes == originals  # already at/above target_s (60) and every scene at its own high end


def test_normalize_episode_targets_never_raises_a_scene_past_its_own_high_end(store):
    """Not enough room anywhere to reach target_s (60): every scene with
    room lands exactly at its own slot's high end (never past it, spec
    'or every scene is at its high end') and a scene already there (the
    hook, no room) is left exactly as it is."""
    m = _new()
    ec = SimpleNamespace(template=templates.load_episode_template("serial_60s_v1"), style_lock=None)
    scenes = [
        {"function": "hook", "target_duration_s": 3.5},  # 1.5-3.5s: already at the high end, no room
        {"function": "setup", "target_duration_s": 5.0},  # 4-8s: 3.0 s of room
        {"function": "cliffhanger", "target_duration_s": 2.0},  # 2-5s: 3.0 s of room
    ]

    before, after = m.script._normalize_episode_targets(ec, scenes)

    assert before == 10.5 and after == 16.5  # short of target_s, and that's fine -- every scene is maxed
    assert [scene["target_duration_s"] for scene in scenes] == [3.5, 8.0, 5.0]


def test_episode_2_waits_for_the_recap_of_episode_1(store):
    m = _new()
    story_id = _ready_story(store)
    llm = FakeLLM()

    message, _ = _failed(m.script, store, story_id, llm=llm, ep=2)

    # Plan 11 stage 4 (DEC-130 amended): the recap comes with episode 1's series memory, which the gate
    # needs written, approved and fresh; it names what is missing.
    assert message == ("Episode 1's series memory is not written yet: approve episode 1's script, then run memory "
                       "for episode 1 and approve it, before writing episode 2.")
    assert llm.calls == []
    assert store.list_episodes(story_id) == []


def test_episode_2_with_the_recap_asks_for_a_recap_scene(store):
    m = _new()
    # series_memory in its spec-2.6 shape: recaps by "epNN", relationships by "<char_a>|<char_b>" -- folded
    # from episode 1's memory entry, which the gate needs (plan 11 stage 4); it opens no hook.
    story_id = _continuity_story(store, entries=((1, _memory_entry(
        "Kiwilo et Mangella se sont alliés en secret.", [],
        deltas={f"{KIWILO}|{MANGELLA}": "publiquement ennemis, secrètement alliés"})),), chosen=None)
    e1 = copy.deepcopy(E1_REPLY)
    e1["scenes"].insert(0, _stub("recap", PARLOIR, "day", [KIWILO], [], "Ce qui s'est passé au parloir.",
                                 "tension", 2.5))
    e3 = dict(E3_FULL, recap={"lines": [{"speaker": KIWILO, "text": "Hier, tout a basculé.", "emotion": "tension",
                                          "delivery": "hushed"}], "on_screen_text": None})
    llm = _script_llm(E1=[e1], E3=[e3], E4=[E4_PASSED])

    _run(m.script, store, story_id, llm=llm, ep=2)

    script = _script(store, story_id, 2)
    assert [s["scene_id"] for s in script["scenes"]][:2] == ["s00", "s01"]
    assert [line["line_id"] for line in _scene(script, "s00")["lines"]] == ["l00"]
    assert "- scenes: exactly 11 entries, one for each of these, in order:\n" in llm.of("E1")[0]["user"]
    assert "Scene 1 — recap\nScene 2 — hook\nScene 3 — body:" in llm.of("E1")[0]["user"]
    # The recap of episode 1 is read by its spec key, "ep01".
    assert "Previous recap: Kiwilo et Mangella se sont alliés en secret." in llm.of("E1")[0]["user"]
    assert "Episode 1 recap: Kiwilo et Mangella se sont alliés en secret." in llm.of("E4")[0]["user"]
    relationship = f"Relationships: {KIWILO}/{MANGELLA}: publiquement ennemis, secrètement alliés"
    assert all(relationship in llm.of(prompt)[0]["user"] for prompt in ("E1", "E3", "E4"))
    assert llm.of("E3")[0]["schema"]["required"] == ["recap", "hook", "cliffhanger", "teaser"]


def test_the_script_step_repairs_dropped_french_elisions_in_every_field(store):
    """spec 4.2, F1: every model-written free text that ends up in
    ``script.json`` is repaired the same way for a French story -- the
    title, a scene's summary, a line's text, a scene's on-screen text, the
    hook's on-screen text, the cliffhanger's reveal, the recap's on-screen
    text and the next-episode teaser (episode 2, so the recap applies)."""
    m = _new()
    story_id = _continuity_story(store, entries=((1, _memory_entry("Un resume.", [])),), chosen=None)
    e1 = copy.deepcopy(E1_REPLY)
    e1["title"] = "L histoire d une île"
    e1["scenes"].insert(0, _stub("recap", PARLOIR, "day", [KIWILO], [], "Ce qui s est passe au parloir.",
                                 "tension", 2.5))
    e1["scenes"][2]["summary"] = "Kiwilo parle d une alliance secrete."  # s02, the first body scene

    def e2_with_elision(call):
        reply = e2_reply(call)
        reply["lines"][0]["text"] = "C est l alliance qu il voulait."
        reply["on_screen_text"] = "l alliance"
        return reply

    e3 = {
        "recap": {"lines": [{"speaker": KIWILO, "text": "Hier soir.", "emotion": "tension", "delivery": "hushed"}],
                  "on_screen_text": "l alliance"},
        "hook": dict(HOOK_PART, on_screen_text="l alliance"),
        "cliffhanger": {"reveal": "C est l alliance d Etat.",
                        "lines": [{"speaker": MANGELLA, "text": "C'est toi.", "emotion": "shocked",
                                   "delivery": "cold"}]},
        "teaser": "Demain, l alliance eclate.",
    }
    llm = _script_llm(E1=[e1], E2=[e2_with_elision] + [e2_reply] * (len(BODY) - 1), E3=[e3], E4=[E4_PASSED])

    _run(m.script, store, story_id, llm=llm, ep=2)

    script = _script(store, story_id, 2)
    assert script["title"] == "L'histoire d'une île"
    assert _scene(script, "s02")["summary"] == "Kiwilo parle d'une alliance secrete."
    assert _scene(script, "s02")["lines"][0]["text"] == "C'est l'alliance qu'il voulait."
    assert _scene(script, "s02")["on_screen_text"] == "l'alliance"
    assert _scene(script, "s00")["on_screen_text"] == "l'alliance"  # the recap scene
    assert script["hook"]["on_screen_text"] == "l'alliance"
    assert script["cliffhanger"]["reveal"] == "C'est l'alliance d'Etat."
    assert script["next_episode_teaser"] == "Demain, l'alliance eclate."


def test_a_story_with_no_props_gets_every_scenes_props_emptied_before_e1_is_checked(store):
    """T2-F9 (live, 2026-09-29): on a story with no props the free tier
    filled every scene's ``props`` with object names ('magnifying glass')
    and E1 failed twice. The ask now says the list is always empty, and the
    reply's props are emptied before its validator runs -- one E1 call, no
    retry, a valid script."""
    m = _new()
    story_id = _ready_story(store, with_prop=False)
    e1 = copy.deepcopy(E1_REPLY)
    for k, scene in enumerate(e1["scenes"]):
        scene["props"] = ["magnifying glass", "notepad"] if k % 2 else ["prop_flashlight"]
    llm = _script_llm(E1=[e1])

    _, log = _run(m.script, store, story_id, llm=llm)

    assert llm.prompts().count("E1") == 1
    assert not any("E1 reply rejected" in line for line in log)
    script = _script(store, story_id)
    assert all(scene["props"] == [] for scene in script["scenes"])
    assert schemas.episode_script_errors(script) == []
    ask = llm.of("E1")[0]["user"]
    assert "- props: always [] -- this story has no props\n" in ask
    assert "Existing props:" not in ask and "0 to 4 of the existing props" not in ask


def test_a_story_with_props_still_has_an_unknown_prop_refused(store):
    """The emptying above is for a story with no props only: with a roster,
    a prop outside it is still refused and asked again (the E1 validator's
    own context check), never silently dropped."""
    m = _new()
    story_id = _ready_story(store)
    wrong = copy.deepcopy(E1_REPLY)
    wrong["scenes"][1]["props"] = ["magnifying glass"]  # s02 (s01 is the hook)
    llm = _script_llm(E1=[wrong, E1_REPLY])

    _, log = _run(m.script, store, story_id, llm=llm)

    assert llm.prompts().count("E1") == 2
    assert any("E1 reply rejected" in line for line in log)
    assert _scene(_script(store, story_id), "s02")["props"] == []


@pytest.mark.parametrize("ep", [0, 9, None])
def test_an_episode_outside_the_season_is_refused(store, ep):
    m = _new()
    story_id = _ready_story(store)
    llm = FakeLLM()

    message, _ = _failed(m.script, store, story_id, llm=llm, ep=ep)

    assert "1 to 8" in message
    assert llm.calls == []


def test_a_story_that_is_not_ready_is_refused(store):
    m = _new()
    story_id = _ready_story(store)
    store.update(story_id, lambda doc: doc["approvals"].update(season=None), now=NOW)
    llm = FakeLLM()

    message, _ = _failed(m.script, store, story_id, llm=llm)

    assert "ready" in message and "season" in message.lower()
    assert llm.calls == []


def test_a_paid_link_is_never_called_and_a_keyless_one_never_built(store):
    """DEC-115's mirror on the script step: groq has no key, openrouter is
    paid while allow_paid is off; only gemini is ever built."""
    from clipping.providers import llm as llm_mod

    m = _new()
    story_id = _ready_story(store)
    settings = {"LLM_CHAIN": "groq/groq-test,gemini/gemini-test,openrouter/test-model",
                "GOOGLE_API_KEY": "test-gemini-key", "OPENROUTER_API_KEY": "test-openrouter-key"}
    replies = {"E1": lambda call: E1_REPLY, "E2": e2_reply, "E3": lambda call: E3_FULL,
               "E4": lambda call: E4_PASSED}
    names = {name: prompt for prompt, name in prompts.SCHEMA_NAMES.items()}
    constructed = []

    class Completions:
        def __init__(self, provider):
            self.provider = provider

        def create(self, **kwargs):
            spec = kwargs["response_format"]["json_schema"]
            call = {"schema": spec["schema"], "user": kwargs["messages"][-1]["content"]}
            content = json.dumps(replies[names[spec["name"]]](call), ensure_ascii=False)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                                   usage=SimpleNamespace(total_tokens=120))

    def factory(link, **kwargs):
        constructed.append(link.provider)
        return SimpleNamespace(chat=SimpleNamespace(completions=Completions(link.provider)))

    def no_sleep(seconds):
        raise AssertionError(f"the chain tried to sleep {seconds}s")

    runner = functools.partial(llm_mod.run_chain, client_factory=factory, sleep_fn=no_sleep)
    # The chain's sleep is faked above, but gemini's rate limiter keeps real
    # time: a whole script's calls in a row waited out its one-minute window
    # for real (60 s). The limiter still paces on this fake clock, instantly;
    # the fixture drops it after the test.
    from clipping.providers import pacing, registry

    clock = [0.0]
    pacing.limiter_for(registry.PROVIDERS["gemini"], time_fn=lambda: clock[0],
                       sleep_fn=lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    ctx, log = _ctx(store, story_id, settings=settings)
    m.script.run(ctx, runner=runner)

    assert clock[0] > 0  # the limiter did pace
    assert constructed == ["gemini"] * (len(BODY) + 3)
    assert log.count("   ⏭ Skipping openrouter/test-model: paid link, allow_paid is off "
                     "(AI Story spends only on opt-in).") == len(BODY) + 3
    assert _script(store, story_id)["consistency_report"]["passed"] is True


def test_a_chain_whose_only_keyed_link_is_paid_sends_nothing(store):
    from clipping.providers import llm as llm_mod

    m = _new()
    story_id = _ready_story(store)
    constructed = []

    def factory(link, **kwargs):
        constructed.append(link.provider)
        raise AssertionError("no client may be built")

    settings = {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model", "OPENROUTER_API_KEY": "test-openrouter-key"}
    runner = functools.partial(llm_mod.run_chain, client_factory=factory)
    message, _ = _failed(m.script, store, story_id, llm=runner, settings=settings)

    assert constructed == []
    assert "allow_paid is off" in message and "GOOGLE_API_KEY" in message
    assert store.list_episodes(story_id) == []


# ============================================================== storyboard

def test_the_storyboard_needs_a_complete_script(store):
    m = _new()
    story_id = _ready_story(store)
    llm = _script_llm(E2=[e2_reply, e2_reply, ProviderError("down"), e2_reply, e2_reply, e2_reply,
                          e2_reply, e2_reply])
    _failed(m.script, store, story_id, llm=llm)

    message, _ = _failed(m.storyboard, store, story_id, llm=FakeLLM(), step="storyboard")
    assert "scene s04 not written yet" in message and "run the script step again" in message
    with pytest.raises(steps.StepFailed):
        m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=Log())


def test_the_fast_storyboard_makes_no_call_and_validates(store):
    m = _new()
    story_id = _written_script(store)
    before = _story_bytes(store, story_id)
    log = Log()

    board = m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=log)

    assert board == _storyboard(store, story_id)
    script = _script(store, story_id)
    lock = store.read_doc(story_id, "style_lock.json")
    template = templates.load_episode_template("serial_60s_v1")
    assert schemas.storyboard_errors(board, min_shot_s=template["min_shot_s"]) == []
    assert schemas.storyboard_context_errors(board, script,
                                             shots_per_scene=lock["episode_defaults"]["shots_per_scene"]) == []
    assert sorted(board["scenes"]) == ALL_SCENES
    assert {entry["source"] for entry in board["scenes"].values()} == {"fast"}
    # The script is re-timed with the storyboard's own transitions; its revision does not move.
    assert script["timing"] == timing.episode_timing(script, template, "fr", style_lock=lock, storyboard=board)
    assert script["rev"] == 1
    assert any(line.startswith("🎞 Storyboard:") for line in log)
    for shot in board["shots"]:
        for name in list(NAMES.values()) + [p["name"] for p in PLACES]:
            assert name not in shot["image_prompt"]
    assert _story_bytes(store, story_id) == before


def test_a_storyboard_never_touches_the_scripts_approval_or_revision(store):
    m = _new()
    story_id = _written_script(store)
    script = _script(store, story_id)
    script["approved_at"] = NOW
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)

    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=Log())
    _run(m.storyboard, store, story_id, llm=FakeLLM(default={"T1": t1_reply}), step="storyboard")
    _regenerate(store, story_id, "shot:1:sh02:plan", llm=FakeLLM(T1r=[t1r_reply]))

    after = _script(store, story_id)
    assert after["approved_at"] == NOW and after["rev"] == 1
    assert after["scenes"] == script["scenes"] and after["consistency_report"] == script["consistency_report"]


def test_the_t1_storyboard_is_one_call_per_scene(store):
    m = _new()
    story_id = _written_script(store)
    before = _story_bytes(store, story_id)
    llm = FakeLLM(default={"T1": t1_reply})

    summary, log = _run(m.storyboard, store, story_id, llm=llm, step="storyboard")

    assert llm.prompts() == ["T1"] * len(ALL_SCENES)
    board = _storyboard(store, story_id)
    assert {entry["source"] for entry in board["scenes"].values()} == {"t1"}
    assert sorted(board["scenes"]) == ALL_SCENES
    assert all(not entry["stale"] for entry in board["scenes"].values())
    # T1 for a scene is shown the two shots planned before it.
    assert "Previous shots:" not in llm.calls[0]["user"]
    assert "Previous shots:\n- wide_establishing / pan_lr\n- medium_two_shot / push_in" in llm.calls[1]["user"]
    assert summary["planned"] == ALL_SCENES
    assert _story_bytes(store, story_id) == before


def test_a_t1_run_replans_only_what_is_fast_and_a_complete_rerun_makes_no_call(store):
    m = _new()
    story_id = _written_script(store)
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=Log())
    llm = FakeLLM(default={"T1": t1_reply})
    _run(m.storyboard, store, story_id, llm=llm, step="storyboard")
    assert len(llm.calls) == len(ALL_SCENES)

    again = FakeLLM()
    _run(m.storyboard, store, story_id, llm=again, step="storyboard")
    assert again.calls == []


def test_a_failed_t1_keeps_the_other_scenes_and_names_the_scene(store):
    m = _new()
    story_id = _written_script(store)
    queue = [t1_reply, t1_reply, ProviderError("down", [("gemini/gemini-test", "HTTP 500")])] + [t1_reply] * 7
    llm = FakeLLM(T1=queue)

    message, _ = _failed(m.storyboard, store, story_id, llm=llm, step="storyboard")

    assert "s03" in message and "run the storyboard step again" in message
    board = _storyboard(store, story_id)
    assert sorted(board["scenes"]) == [sid for sid in ALL_SCENES if sid != "s03"]
    again = FakeLLM(T1=[t1_reply])
    _run(m.storyboard, store, story_id, llm=again, step="storyboard")
    assert again.prompts() == ["T1"] and "Broccolia interroge" in again.calls[0]["user"]
    assert sorted(_storyboard(store, story_id)["scenes"]) == ALL_SCENES


# ============================================================== regenerate

def test_regenerating_a_body_scene_touches_only_it_and_stales_its_storyboard_scene(store):
    m = _new()
    story_id = _written_script(store)
    _run(m.storyboard, store, story_id, llm=FakeLLM(default={"T1": t1_reply}), step="storyboard")
    # Both documents approved.
    script = _script(store, story_id)
    script["approved_at"] = NOW
    store.write_episode_doc(story_id, 1, "script.json", script, now=NOW)
    board = _storyboard(store, story_id)
    board["approved_at"] = NOW
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=NOW)
    before_script, before_board = _script(store, story_id), _storyboard(store, story_id)
    story_before = _story_bytes(store, story_id)

    llm = FakeLLM(E2=[functools.partial(e2_reply, text="Je sais tout, Mangella.")])
    _, log = _regenerate(store, story_id, "scene:1:s03", llm=llm, note="Plus de menace.")

    assert llm.prompts() == ["E2"]
    assert "Follow the author's note: Plus de menace." in llm.calls[0]["user"]
    script = _script(store, story_id)
    s03 = _scene(script, "s03")
    assert s03["lines"][0]["text"] == "Je sais tout, Mangella."
    assert [line["line_id"] for line in s03["lines"]] == ["l12", "l13"]
    assert s03["rev"] == 2 and script["rev"] == 2
    assert script["approved_at"] is None and script["approved_anyway"] is None
    assert script["consistency_report"]["stale"] is True
    for sid in ALL_SCENES:
        if sid != "s03":
            assert _scene(script, sid) == _scene(before_script, sid)
    for key in ("title", "hook", "cliffhanger", "next_episode_teaser"):
        assert script[key] == before_script[key]
    board = _storyboard(store, story_id)
    assert board["approved_at"] is None
    assert board["scenes"]["s03"]["stale"] is True
    assert all(not board["scenes"][sid]["stale"] for sid in ALL_SCENES if sid != "s03")
    assert board["shots"] == before_board["shots"]
    assert _story_bytes(store, story_id) == story_before
    assert any(line.startswith("🔁 Regenerated scene:1:s03") for line in log)

    # A T1 run plans that scene alone again.
    t1 = FakeLLM(T1=[t1_reply])
    _run(m.storyboard, store, story_id, llm=t1, step="storyboard")
    assert t1.prompts() == ["T1"] and "Broccolia interroge" in t1.calls[0]["user"]
    board = _storyboard(store, story_id)
    assert board["scenes"]["s03"] == {"source": "t1", "script_rev": 2, "stale": False}


def test_a_failed_regenerate_changes_nothing(store):
    m = _new()
    story_id = _written_script(store)
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=Log())
    folder = Path(store.episode_dir(story_id, 1))
    before = {name: (folder / name).read_bytes() for name in ("script.json", "storyboard.json")}
    llm = FakeLLM(E2=[ProviderError("down", [("gemini/gemini-test", "HTTP 503")])])

    message, _ = _failed(m.regenerate, store, story_id, llm=llm, step="regenerate", ep=None,
                         params={"target": "scene:1:s03", "note": "Plus de menace."})

    assert message.startswith("Cannot regenerate 'scene:1:s03': E2: every provider in the chain failed")
    assert {name: (folder / name).read_bytes() for name in before} == before


def test_an_episode_keeps_the_template_it_was_written_against(store):
    m = _new()
    story_id = _written_script(store)
    store.update(story_id, lambda doc: doc.update(episode_template_id="serial_90s_v1"), now=NOW)
    llm = FakeLLM()

    _run(m.script, store, story_id, llm=llm)

    assert llm.calls == []
    assert _script(store, story_id)["template_id"] == "serial_60s_v1"
    assert _script(store, story_id)["timing"]["window_s"] == [55, 80]


def test_regenerating_a_framing_scene_runs_its_partial_e3(store):
    m = _new()
    story_id = _written_script(store)
    before = _script(store, story_id)
    new_cliff = dict(CLIFF_PART, reveal="Le téléphone affiche le nom de Mangella.")
    llm = FakeLLM(E3=[{"cliffhanger": new_cliff}])

    _regenerate(store, story_id, "scene:1:s10", llm=llm)

    assert llm.prompts() == ["E3"]
    assert llm.calls[0]["schema"]["required"] == ["cliffhanger"]
    script = _script(store, story_id)
    assert script["cliffhanger"]["reveal"] == new_cliff["reveal"]
    assert _scene(script, "s10")["rev"] == 2
    assert script["hook"] == before["hook"] and script["next_episode_teaser"] == before["next_episode_teaser"]


def test_regenerating_the_hook_changes_only_the_hook(store):
    m = _new()
    story_id = _written_script(store)
    before = _script(store, story_id)
    new_hook = {"lines": [{"speaker": MANGELLA, "text": "Personne ne dort ce soir.", "emotion": "tension",
                           "delivery": "icy"}], "on_screen_text": "Nuit blanche au parloir"}
    llm = FakeLLM(E3=[{"hook": new_hook}])

    _regenerate(store, story_id, "hook:1", llm=llm, note="Plus froid.")

    assert llm.prompts() == ["E3"] and "Follow the author's note: Plus froid." in llm.calls[0]["user"]
    script = _script(store, story_id)
    assert script["hook"] == {"on_screen_text": "Nuit blanche au parloir"}
    hook = _scene(script, "s01")
    assert [(line["line_id"], line["text"]) for line in hook["lines"]] == [("l04", "Personne ne dort ce soir.")]
    assert hook["source"] == "E3" and hook["rev"] == 2
    changed = {"hook", "scenes", "rev", "timing", "consistency_report", "updated_at"}
    for key in before:
        if key not in changed:
            assert script[key] == before[key], key
    assert [s for s in script["scenes"] if s["scene_id"] != "s01"] == [
        s for s in before["scenes"] if s["scene_id"] != "s01"]
    assert script["consistency_report"]["stale"] is True


def test_regenerating_the_teaser_changes_only_the_teaser(store):
    m = _new()
    story_id = _written_script(store)
    before = _script(store, story_id)
    llm = FakeLLM(E3=[{"teaser": "Demain, le téléphone se tait pour toujours."}])

    _regenerate(store, story_id, "teaser:1", llm=llm)

    script = _script(store, story_id)
    assert script["next_episode_teaser"] == "Demain, le téléphone se tait pour toujours."
    assert script["scenes"] == before["scenes"] and script["hook"] == before["hook"]
    assert script["rev"] == 2


def test_replanning_one_shot_is_one_t1r_call_and_changes_only_that_shot(store):
    m = _new()
    story_id = _written_script(store)
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=Log())
    before = _storyboard(store, story_id)
    script_before = _script(store, story_id)
    llm = FakeLLM(T1r=[t1r_reply])

    _regenerate(store, story_id, "shot:1:sh05:plan", llm=llm, note="Plus intime.")

    assert llm.prompts() == ["T1r"] and "Follow the author's note: Plus intime." in llm.calls[0]["user"]
    board = _storyboard(store, story_id)
    assert len(board["shots"]) == len(before["shots"])
    for old, new in zip(before["shots"], board["shots"]):
        keys = ("shot_id", "scene_id", "framing", "action", "lines", "subject_tags")
        if old["shot_id"] == "sh05":
            assert new["action"].endswith("leans in, whispering a secret.")
            assert new["lines"] == old["lines"]
        else:
            assert {k: new[k] for k in keys} == {k: old[k] for k in keys}
    assert board["rev"] == before["rev"] + 1
    script = _script(store, story_id)
    assert script["rev"] == script_before["rev"] and script["scenes"] == script_before["scenes"]


@pytest.mark.parametrize("target,named", [
    ("scene:1:s42", "s42"), ("shot:1:sh99:plan", "sh99"), ("scene:3:s02", "episode 3"),
])
def test_an_unknown_scene_shot_or_episode_is_refused_by_name(store, target, named):
    m = _new()
    story_id = _written_script(store)
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=Log())
    llm = FakeLLM()

    message, _ = _failed(m.regenerate, store, story_id, llm=llm, step="regenerate", ep=None,
                         params={"target": target})

    assert message.startswith(f"Cannot regenerate {target!r}: ") and named in message
    assert llm.calls == []


@pytest.mark.parametrize("target,parsed", [
    ("scene:1:s03", ("scene", 1, "s03")), ("hook:2", ("hook", 2)), ("cliffhanger:12", ("cliffhanger", 12)),
    ("teaser:1", ("teaser", 1)), ("shot:1:sh05:plan", ("shot", 1, "sh05")),
    ("scene:0:s03", None), ("scene:1:s3", None), ("shot:1:sh05", ("shot_image", 1, "sh05")),
    ("shot:1:sh05:video", ("shot_video", 1, "sh05")), ("shot:1:sh05:frames", None), ("hook:x", None), ("line:1:l04", ("line", 1, "l04")), (None, None),
])
def test_the_episode_target_grammar(target, parsed):
    m = _new()
    assert m.regen.parse_episode_target(target) == parsed


def test_the_web_layers_parser_reads_the_episode_targets_since_stage_8():
    """The web layer reads ``parse_target``; the episode targets reached it in stage 8."""
    m = _new()
    for target in ("scene:1:s03", "hook:1", "shot:1:sh05:plan"):
        assert m.regenerate.parse_target(target) == m.regen.parse_episode_target(target) is not None


# ============================================================ helpers + registry

def test_mark_changed_bumps_revisions_clears_approvals_and_stales_moved_scenes(store):
    m = _new()
    story_id = _written_script(store)
    m.storyboard.build_fast(store, story_id, 1, now=NOW, on_log=Log())
    script, board = _script(store, story_id), _storyboard(store, story_id)
    script["approved_at"] = board["approved_at"] = NOW
    script["approved_anyway"] = NOW

    m.common.mark_changed(script, board, scene_ids=["s02"], now=NOW)

    assert script["rev"] == 2 and _scene(script, "s02")["rev"] == 2 and _scene(script, "s03")["rev"] == 1
    assert script["approved_at"] is None and script["approved_anyway"] is None
    assert script["consistency_report"]["stale"] is True
    assert board["approved_at"] is None
    assert [sid for sid, entry in board["scenes"].items() if entry["stale"]] == ["s02"]


def test_retime_is_derived_and_never_moves_the_revision(store):
    m = _new()
    story_id = _written_script(store)
    script = _script(store, story_id)
    script["approved_at"] = NOW
    script["timing"] = None
    ctx, _ = _ctx(store, story_id)
    ec = m.common.load_episode_context(ctx)

    m.common.retime(script, ec)

    assert script["timing"]["total_s"] > 0 and script["rev"] == 1 and script["approved_at"] == NOW


def test_the_episode_steps_are_registered():
    for name in ("script", "storyboard"):
        assert name in steps.RUNNERS and callable(steps.RUNNERS[name])


# ================================================================== phase 5 stage 3: continuity
#
# Plan 11 stage 3 (DEC-177): from episode 2 on, E1 is handed the hooks open
# when the episode starts (series_memory.open_hooks_before: the fold of the
# entries before it, never the stored open_hooks, which folds later entries
# too) and the direction chosen on the previous episode's feedback, and marks
# pays_off; the script keeps it; E3's recap is written from the recap; a
# deterministic pre-check runs before E4 and records a hook_payoff issue in
# the consistency report when no body scene pays off an open hook or a scene
# names one that is not open; E4 judges the lines of the scenes that do.

HOOK_PHONE = "Qui a volé le téléphone ?"
HOOK_BETRAY = "Kiwilo va-t-il trahir Mangella ?"
HOOK_VOTE = "Le vote est-il truqué ?"
DIRECTIONS = ["Plus de Broccolia.", "Un vote truqué, sous les yeux de tous.", "Kiwilo seul contre tous."]


def _memory_entry(recap, opened, closed=(), deltas=None):
    return {"recap": recap, "hooks_opened": list(opened), "hooks_closed": list(closed),
            "relationship_deltas": dict(deltas or {}), "script_rev": 1, "at": NOW, "approved_at": NOW}


EP1_ENTRY = _memory_entry("Kiwilo et Mangella se sont alliés en secret.", [HOOK_PHONE, HOOK_BETRAY],
                          deltas={f"{KIWILO}|{MANGELLA}": "alliés en secret"})
# Episode 2's own memory, as if a first draft of it had been through the
# memory step: it closes the phone and opens the vote -- in the stored,
# folded open_hooks, never in what episode 2 starts with.
EP2_ENTRY = _memory_entry("Le téléphone retrouvé, le vote approche.", [HOOK_VOTE], [HOOK_PHONE])


def _continuity_story(store, *, entries=((1, EP1_ENTRY),), chosen=1, v2=False):
    from clipping.aistory import series_memory

    story_id = _ready_story(store, v2=v2)
    # Episode 1's script, at the revision the entries record (1): the gate needs its memory fresh (plan 11
    # stage 4). Episode 1 is legacy-shaped even on a v2 story (new_objects is offered from episode 2 on).
    # Phase 7 stage 6a (DEC-230/231), re-pinned on purpose: a v2 story's replies are the v2 fixture's.
    _run(_new().script, store, story_id, llm=_script_llm(v2=v2))
    season = store.read_doc(story_id, "season.json")
    for ep, entry in entries:
        season = series_memory.merge_entry(season, ep, entry)
    season["audience_feedback"] = [{"ep": 1, "pasted_at": NOW, "text": "Top commentaires : on veut un vote !",
                                    "digest": "Le public veut du vote.", "directions": list(DIRECTIONS),
                                    "chosen_direction": chosen}]
    store.write_doc(story_id, "season.json", season, now=NOW)
    assert store.get(story_id)["status"] == "ready"
    return story_id


def _e1_ep2_paying(pays_off):
    """E1_REPLY with the recap stub first (episode 2) and every scene's
    pays_off: [] unless *pays_off* ({scene id: [hooks]}) names one."""
    e1 = copy.deepcopy(E1_REPLY)
    e1["scenes"].insert(0, _stub("recap", PARLOIR, "day", [KIWILO], [], "Ce qui s'est passé au parloir.",
                                 "tension", 2.5))
    ids = ["s00"] + ALL_SCENES
    for sid, scene in zip(ids, e1["scenes"]):
        scene["pays_off"] = list(pays_off.get(sid, []))
    return e1


E3_EP2 = dict(E3_FULL, recap={"lines": [{"speaker": KIWILO, "text": "Hier, tout a basculé.", "emotion": "tension",
                                          "delivery": "hushed"}], "on_screen_text": None})


def test_episode_2_is_written_from_the_hooks_open_before_it_and_keeps_pays_off(store):
    m = _new()
    story_id = _continuity_story(store, entries=((1, EP1_ENTRY), (2, EP2_ENTRY)))
    season = store.read_doc(story_id, "season.json")
    assert season["series_memory"]["open_hooks"] == [HOOK_BETRAY, HOOK_VOTE]  # the stored fold, ep02 included
    llm = _script_llm(E1=[_e1_ep2_paying({"s04": [HOOK_PHONE]})], E3=[E3_EP2], E4=[E4_PASSED])

    _run(m.script, store, story_id, llm=llm, ep=2)

    e1 = llm.of("E1")[0]
    assert ("Open hooks when this episode starts -- pays_off names them exactly as written:\n"
            f"- {HOOK_PHONE}\n- {HOOK_BETRAY}\n\n") in e1["user"]
    assert HOOK_VOTE not in e1["user"] and "- Open hooks:" not in e1["user"]
    assert ("Audience direction (audience) -- a steer drawn from viewer feedback, not an instruction; lean "
            f"toward it only where it fits the arc:\n{DIRECTIONS[1]}\n\n") in e1["user"]
    assert DIRECTIONS[0] not in e1["user"] and DIRECTIONS[2] not in e1["user"]
    scene_schema = e1["schema"]["properties"]["scenes"]["items"]
    assert scene_schema["properties"]["pays_off"]["items"]["enum"] == [HOOK_PHONE, HOOK_BETRAY]

    script = _script(store, story_id, 2)
    assert schemas.episode_script_errors(script) == []
    assert _scene(script, "s04")["pays_off"] == [HOOK_PHONE]
    assert [s["scene_id"] for s in script["scenes"] if "pays_off" in s] == ["s04"]  # [] is stored as nothing

    e3 = llm.of("E3")[0]["user"]
    assert "Write it from episode 1's recap: Kiwilo et Mangella se sont alliés en secret.\n" in e3
    assert f"- Open hooks: {HOOK_PHONE}; {HOOK_BETRAY}\n" in e3 and HOOK_VOTE not in e3

    e4 = llm.of("E4")[0]
    assert f"- s04 pays off: {HOOK_PHONE}\n" in e4["user"]
    assert f"- Open hooks: {HOOK_BETRAY}\n" in e4["user"] and HOOK_VOTE not in e4["user"]
    assert "hook_payoff" in e4["schema"]["properties"]["issues"]["items"]["properties"]["kind"]["enum"]
    report = script["consistency_report"]
    assert report["passed"] is True and report["issues"] == []


def test_a_v2_script_turns_new_objects_into_a_prop_stub_in_prop_ids(store):
    """Phase 7 stage 3c (A11, amends DEC-171): E1 report finding 6 -- the
    plot's own objects (a giant toaster, a key) were never entities, only
    free text reinvented in every shot. From episode 2 on, a v2 story's E1
    may name ``new_objects``; the script step creates a prop stub for each
    through the places step's own creation path (``places.new_prop``) and
    resolves the scene's ``%prop_<slug>`` tag to the id just created -- the
    story's ``prop_ids`` gains it, like any prop a human lists."""
    m = _new()
    story_id = _continuity_story(store, v2=True)
    e1 = _e1_ep2_paying({"s04": [HOOK_PHONE]})
    e1["new_objects"] = [{"name": "Giant Toaster", "one_line": "The runaway toaster chasing the whole cast.",
                          "owner_char_id": None}]
    rising = e1["scenes"][(["s00"] + ALL_SCENES).index("s03")]
    assert rising["props"] == []  # the base fixture's s03 (rising) has no prop
    rising["props"] = ["%prop_giant_toaster"]
    # Phase 7 stage 6a (DEC-230/231), re-pinned on purpose: the v2 fixture (no repeated line, J1 answered).
    llm = _script_llm(v2=True, E1=[e1], E3=[E3_EP2], E4=[E4_PASSED])

    assert "prop_giant_toaster" not in store.get(story_id)["prop_ids"]

    _run(m.script, store, story_id, llm=llm, ep=2)

    story = store.get(story_id)
    assert "prop_giant_toaster" in story["prop_ids"]
    assert PHONE in story["prop_ids"]  # the existing prop is kept, not replaced
    prop = store.read_entity(story_id, "props", "prop_giant_toaster")
    assert prop["name"] == "Giant Toaster"
    assert prop["one_line"] == "The runaway toaster chasing the whole cast."
    assert prop["owner_char_id"] is None
    # No text or image yet: R1 (and, v2, R1v2) write them at the next places
    # step run, the same gate as any other prop with no text (places.py).
    assert prop["descriptor"] is None and prop["image"] is None

    script = _script(store, story_id, 2)
    assert _scene(script, "s03")["props"] == ["prop_giant_toaster"]
    assert schemas.episode_script_errors(script) == []


def test_without_a_chosen_direction_e1_has_no_audience_block(store):
    m = _new()
    story_id = _continuity_story(store, chosen=None)
    llm = _script_llm(E1=[_e1_ep2_paying({"s03": [HOOK_BETRAY]})], E3=[E3_EP2], E4=[E4_PASSED])

    _run(m.script, store, story_id, llm=llm, ep=2)

    assert "Audience direction" not in llm.of("E1")[0]["user"]
    assert _scene(_script(store, story_id, 2), "s03")["pays_off"] == [HOOK_BETRAY]


def test_an_e1_reply_paying_off_nothing_is_asked_again_then_accepted(store):
    m = _new()
    story_id = _continuity_story(store)
    llm = _script_llm(E1=[_e1_ep2_paying({}), _e1_ep2_paying({"s05": [HOOK_BETRAY]})], E3=[E3_EP2],
                      E4=[E4_PASSED])

    _summary, log = _run(m.script, store, story_id, llm=llm, ep=2)

    assert llm.prompts()[:2] == ["E1", "E1"]
    assert any("E1 reply rejected" in line and "no body scene pays off" in line for line in log)
    assert _scene(_script(store, story_id, 2), "s05")["pays_off"] == [HOOK_BETRAY]


def test_a_framing_scene_paying_off_a_hook_is_cleared_before_validation(store):
    """Tier-2 finding T2-P5-F7 (2026-09-30): on the live French story E1 put
    pays_off on the hook scene s01 in 4 of 4 runs, and E4 then judged that a
    hook scene does not pay off last episode's hook. Only a body scene pays a
    hook off: a recap, hook or cliffhanger scene's pays_off is emptied before
    validation (like T2-F9's props repair) -- no retry, the body scene's kept."""
    m = _new()
    story_id = _continuity_story(store)
    llm = _script_llm(E1=[_e1_ep2_paying({"s00": [HOOK_PHONE], "s01": [HOOK_PHONE], "s05": [HOOK_BETRAY]})],
                      E3=[E3_EP2], E4=[E4_PASSED])

    _summary, log = _run(m.script, store, story_id, llm=llm, ep=2)

    assert llm.prompts().count("E1") == 1
    script = _script(store, story_id, 2)
    assert [s["scene_id"] for s in script["scenes"] if "pays_off" in s] == ["s05"]
    assert _scene(script, "s05")["pays_off"] == [HOOK_BETRAY]


def test_the_payoff_pre_check_records_a_hook_payoff_issue_and_e4_still_runs(store):
    """The script was written paying off the phone; the memory of episode 1
    is then written again without that hook. The next consistency check
    runs the pre-check first: no body scene pays off a hook open before
    episode 2, and s04 names one that is not open -- two hook_payoff issues
    in the report, E4's own verdict (passed) kept for the rest, the report
    failed."""
    from clipping.aistory import series_memory

    m = _new()
    story_id = _continuity_story(store)
    _run(m.script, store, story_id, llm=_script_llm(E1=[_e1_ep2_paying({"s04": [HOOK_PHONE]})], E3=[E3_EP2],
                                                    E4=[E4_PASSED]), ep=2)
    season = series_memory.merge_entry(store.read_doc(story_id, "season.json"), 1,
                                       dict(EP1_ENTRY, hooks_opened=[HOOK_BETRAY]))
    store.write_doc(story_id, "season.json", season, now=NOW)
    script = _script(store, story_id, 2)
    script["consistency_report"]["stale"] = True
    store.write_episode_doc(story_id, 2, "script.json", script, now=NOW)
    llm = FakeLLM(E4=[E4_PASSED])

    _summary, log = _run(m.script, store, story_id, llm=llm, ep=2)

    assert llm.prompts() == ["E4"]
    report = _script(store, story_id, 2)["consistency_report"]
    assert report["passed"] is False and report["stale"] is False
    assert [(issue["scene_id"], issue["kind"]) for issue in report["issues"]] == [(None, "hook_payoff"),
                                                                                  ("s04", "hook_payoff")]
    assert "No body scene pays off" in report["issues"][0]["fix"] and "episode 2" in report["issues"][0]["fix"]
    assert HOOK_PHONE in report["issues"][1]["fix"] and "not open before episode 2" in report["issues"][1]["fix"]
    e4 = llm.of("E4")[0]
    assert "Hook payoffs" not in e4["user"]  # nothing left to judge: the only named hook is not open
    assert "hook_payoff" not in e4["schema"]["properties"]["issues"]["items"]["properties"]["kind"]["enum"]
    assert f"- Open hooks: {HOOK_BETRAY}\n" in e4["user"]
    assert any("Hook payoff check: 2 issues" in line for line in log)
    assert any("Consistency: 2 issues" in line for line in log)


def test_e4_of_episode_2_reads_only_the_recaps_before_it(store):
    """Episode 2's own memory entry exists (a first draft went through the
    memory step): its recap is in the stored recaps, never in what E4 checks
    episode 2 against."""
    m = _new()
    story_id = _continuity_story(store, entries=((1, EP1_ENTRY), (2, EP2_ENTRY)))
    llm = _script_llm(E1=[_e1_ep2_paying({"s04": [HOOK_PHONE]})], E3=[E3_EP2], E4=[E4_PASSED])

    _run(m.script, store, story_id, llm=llm, ep=2)

    user = llm.of("E4")[0]["user"]
    assert f"- Episode 1 recap: {EP1_ENTRY['recap']}\n" in user
    assert EP2_ENTRY["recap"] not in user and "Episode 2 recap" not in user


# ------------------------------------------------------------ E1's cap

def test_an_episode_1_e1_call_keeps_the_registry_cap(store):
    m = _new()
    story_id = _ready_story(store)
    llm = _script_llm()

    _run(m.script, store, story_id, llm=llm)

    assert [call["max_tokens"] for call in llm.of("E1")] == [prompts.MAX_TOKENS["E1"]] == [1450]


def test_an_episode_2_e1_call_with_no_hook_open_keeps_the_registry_cap(store):
    m = _new()
    story_id = _continuity_story(store, entries=((1, dict(EP1_ENTRY, hooks_opened=[])),))
    e1 = _e1_ep2_paying({})
    for scene in e1["scenes"]:
        del scene["pays_off"]
    llm = _script_llm(E1=[e1], E3=[E3_EP2], E4=[E4_PASSED])

    _run(m.script, store, story_id, llm=llm, ep=2)

    call = llm.of("E1")[0]
    assert "pays_off" not in call["user"] and "Audience direction (audience)" in call["user"]
    assert call["max_tokens"] == 1450


def test_an_episode_2_e1_call_paying_off_a_hook_gets_the_payoff_cap_on_every_attempt(store):
    m = _new()
    story_id = _continuity_story(store)
    llm = _script_llm(E1=[_e1_ep2_paying({}), _e1_ep2_paying({"s05": [HOOK_BETRAY]})], E3=[E3_EP2],
                      E4=[E4_PASSED])

    _summary, log = _run(m.script, store, story_id, llm=llm, ep=2)

    assert [call["max_tokens"] for call in llm.of("E1")] == [prompts.E1_PAYOFF_MAX_TOKENS] * 2 == [2210, 2210]
    assert any(line.startswith("✍️ E1 via") and "(cap 2210)" in line for line in log)
    # Only E1 takes the variant's cap: E2, E3 and E4 keep their own.
    assert {call["max_tokens"] for call in llm.of("E2")} == {prompts.MAX_TOKENS["E2"]}
    assert llm.of("E3")[0]["max_tokens"] == prompts.MAX_TOKENS["E3"]
    assert llm.of("E4")[0]["max_tokens"] == prompts.MAX_TOKENS["E4"]


def test_call_json_sends_the_registry_cap_unless_handed_one_the_same_on_the_retry():
    from clipping.aistory.steps import llm_call

    ctx, _log = _ctx(SimpleNamespace(outputs_dir="/nonexistent"), "0123456789ab")
    replies = []

    def runner(chain, **kwargs):
        replies.append(kwargs["max_tokens"])
        return {"n": len(replies)}, LINK

    validator = lambda value: [] if value["n"] % 2 == 0 else ["the first answer is refused"]  # noqa: E731
    llm_call.call_json(ctx, "E1", "system", "user", {}, validator=validator, runner=runner)
    assert replies == [1450, 1450]
    replies.clear()
    llm_call.call_json(ctx, "E1", "system", "user", {}, validator=validator, runner=runner, max_tokens=None)
    assert replies == [1450, 1450]
    replies.clear()
    llm_call.call_json(ctx, "E1", "system", "user", {}, validator=validator, runner=runner, max_tokens=2210)
    assert replies == [2210, 2210]


def test_regenerating_the_recap_writes_it_from_the_recap(store):
    m = _new()
    story_id = _continuity_story(store)
    _run(m.script, store, story_id, llm=_script_llm(E1=[_e1_ep2_paying({"s04": [HOOK_PHONE]})], E3=[E3_EP2],
                                                    E4=[E4_PASSED]), ep=2)
    llm = FakeLLM(E3=[{"recap": E3_EP2["recap"]}])

    _regenerate(store, story_id, "scene:2:s00", llm=llm)

    user = llm.of("E3")[0]["user"]
    assert "Write it from episode 1's recap: Kiwilo et Mangella se sont alliés en secret.\n" in user
    assert f"- Open hooks: {HOOK_PHONE}; {HOOK_BETRAY}\n" in user
    assert _scene(_script(store, story_id, 2), "s04")["pays_off"] == [HOOK_PHONE]  # a rewrite keeps it


def test_apply_e1_persists_pays_off_once_and_nothing_for_an_empty_list(store):
    m = _new()
    story_id = _continuity_story(store)
    ec = m.common.load_context(store, story_id, 2)
    script = m.script.skeleton(ec, now=NOW)
    reply = _e1_ep2_paying({"s02": [HOOK_BETRAY, HOOK_BETRAY], "s04": [HOOK_PHONE]})

    m.script.apply_e1(ec, script, reply)

    assert _scene(script, "s02")["pays_off"] == [HOOK_BETRAY]
    assert _scene(script, "s04")["pays_off"] == [HOOK_PHONE]
    assert all("pays_off" not in scene for scene in script["scenes"] if scene["scene_id"] not in ("s02", "s04"))
    assert m.common.trial_errors(ec, script) == []


# ------------------------------------------------------------ the pre-check (pure)

def _pre_script(*scenes):
    """A script reduced to what the pre-check reads: each scene's id,
    function and optional pays_off."""
    out = []
    for sid, function, *paid in scenes:
        scene = {"scene_id": sid, "function": function}
        if paid:
            scene["pays_off"] = paid[0]
        out.append(scene)
    return {"scenes": out}


def test_payoff_pre_check_passes_when_a_body_scene_pays_off_an_open_hook():
    m = _new()
    script = _pre_script(("s00", "recap"), ("s01", "hook"), ("s02", "setup", [HOOK_PHONE]), ("s03", "cliffhanger"))
    assert m.script.payoff_issues(script, 2, [HOOK_PHONE, HOOK_BETRAY]) == []
    # Nothing open, nothing named: nothing to check (episode 1, or every hook closed).
    plain = _pre_script(("s01", "hook"), ("s02", "setup"), ("s03", "cliffhanger"))
    assert m.script.payoff_issues(plain, 1, []) == []
    assert m.script.payoff_issues(plain, 3, []) == []


def test_payoff_pre_check_no_body_scene_paying_off():
    m = _new()
    for script in (_pre_script(("s00", "recap"), ("s01", "hook"), ("s02", "setup"), ("s03", "cliffhanger")),
                   _pre_script(("s00", "recap"), ("s01", "hook", [HOOK_PHONE]), ("s02", "setup", []),
                               ("s03", "cliffhanger", [HOOK_BETRAY]))):
        issues = m.script.payoff_issues(script, 2, [HOOK_PHONE, HOOK_BETRAY])
        assert [(i["scene_id"], i["kind"]) for i in issues] == [(None, "hook_payoff")], issues
        assert issues[0]["fix"].startswith("No body scene pays off one of the 2 hooks open before episode 2")


def test_payoff_pre_check_a_hook_that_is_not_open():
    m = _new()
    script = _pre_script(("s00", "recap"), ("s01", "hook"), ("s02", "setup", [HOOK_PHONE]),
                         ("s03", "rising", [HOOK_VOTE]), ("s04", "cliffhanger"))
    issues = m.script.payoff_issues(script, 2, [HOOK_PHONE])
    assert [(i["scene_id"], i["kind"]) for i in issues] == [("s03", "hook_payoff")]
    assert issues[0]["fix"] == f"Scene s03 pays off “{HOOK_VOTE}”, which is not open before episode 2."
    # Named with no hook open at all (every one closed since): the same issue, and no "no payoff" one.
    issues = m.script.payoff_issues(_pre_script(("s01", "hook"), ("s02", "setup", [HOOK_VOTE])), 3, [])
    assert [(i["scene_id"], i["kind"]) for i in issues] == [("s02", "hook_payoff")]


def test_payoff_pre_check_issues_fit_the_stored_report():
    """Every fix fits the report's 300 characters; one issue per scene at
    most, plus one for the whole episode: at most 13 for 12 scenes, which
    with E4's own 6 stays under the report's 20."""
    m = _new()
    long_hooks = [f"{i} " + "x" * 117 for i in range(4)]
    scenes = [(f"s{i:02d}", "setup", list(long_hooks)) for i in range(2, 12)]
    script = _pre_script(("s00", "recap", list(long_hooks)), ("s01", "hook", list(long_hooks)), *scenes)
    issues = m.script.payoff_issues(script, 2, ["un autre crochet"])
    assert len(issues) == 13
    assert all(len(issue["fix"]) <= 300 for issue in issues)
    report = {"passed": False, "issues": issues + [{"scene_id": None, "kind": "other", "fix": "x"}] * 6,
              "checked_rev": 1, "checked_at": NOW, "stale": False}
    errors = schemas.validate(report, schemas.EPISODE_SCRIPT_SCHEMA["properties"]["consistency_report"])
    assert errors == []


# ------------------------------------------------------------ RC-M1 at the step

# sha256 of json.dumps([system, user, schema, max_tokens]) of the E1, E3 and E4
# requests an episode-1 script run sends, rendered by HEAD 0ae8efb's step
# (before this stage): the cap is pinned too -- the limiter reserves input +
# max_tokens, so a raised cap changes an episode-1 call as surely as its text.
RC_M1_STEP_SHAS = {
    "E1": "fa6931fbc0a96213f6443d759fec60e34e25861e0090d8753f1fc68a80fe1ce2",
    "E3": "8e66f56f1ea8429ec26c4dff29cd2f08d26b556a090bd33cc2bb72fc1b2427dd",
    "E4": "eb481a5184e0fd7d7f0188798c07d11907b09085f830ba1e259d0f85cc57cad1",
}


def _request_sha(call):
    blob = json.dumps([call["system"], call["user"], call["schema"], call["max_tokens"]], ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def test_rc_m1_an_episode_1_script_run_sends_the_same_e1_e3_e4_as_head(store):
    m = _new()
    story_id = _ready_story(store)
    llm = _script_llm()

    _run(m.script, store, story_id, llm=llm)

    assert {prompt: _request_sha(llm.of(prompt)[0]) for prompt in ("E1", "E3", "E4")} == RC_M1_STEP_SHAS
    assert all("pays_off" not in scene for scene in _script(store, story_id)["scenes"])
