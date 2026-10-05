"""Agent mode: the ``story-fast-track`` step -- one job takes a story created
in agent mode from its seed to episode 1 rendered with its metadata pack,
approving each document by rule (plan 21 stage 1; RC-G1, RC-A3).

The story is French, on the free chain, ``prompt_only`` (the sheets are text
to image) and on the fruit_drama style, created with ``mode: "agent"`` and a
one-line seed. Every call answers through a fake: the LLM through phase 3's
``FakeLLM`` -- C1 (one concept whose sketch names the episode fixture's
cast: Kiwilo, Mangella, Broccolia), B1-B3 (phase 1's replies), K1 by name,
P0 (the episode fixture's two places and its prop, so the ids are the ones
E1's reply names), P1 by name, R1, S1/S2 for eight episodes, then the fast
track's own E1-E4, T1 and M1 -- images through stage 8's recording
``FakeImage``, voices through the measurement's fake Edge, ffmpeg through
stage 7's ``FakeFFmpeg``, the cover through stage 9's ``FakeCover``. The
whole chain ends with **episode 1 rendered on the fake renderer** (the
fixtures allow it end to end). Offline and hermetic: stage 8's own
``hermetic`` fixture.

Each part is idempotent: a stop at each one, then a run again, makes
together exactly the calls one run makes (counted per prompt and per image).

The step module is imported inside the tests, so on the parent commit each
test fails on its own. Stdlib + pytest (DEC-012); the API tests skip without
fastapi.
"""

from __future__ import annotations

import collections
import copy

import pytest

import test_story_assets_step as tas
import test_story_cast_steps as tcs
import test_story_episode_steps as eps
import test_story_fast_track as tft
import test_story_metadata_step as tms
import test_story_steps as tss
from clipping.aistory import defaults, prompts, schemas, steps
from clipping.cancel import CancelToken
from clipping.providers.errors import ProviderError
from test_stories_api_phase4 import api  # noqa: F401 -- the throwaway app (skips without fastapi)
from test_story_assets_step import hermetic, store  # noqa: F401 -- stage 8's fixtures, used as they are
from test_story_step_jobs import _register, _step_job, job_store, worker  # noqa: F401 -- the worker's fixtures

NOW = eps.NOW
SEED = "Une île de téléréalité où des fruits en couple se trahissent pour rester à l'écran."
# Plan 28 stage B2: no Edge voice is proposed any more, so the agent's cast speaks through a local engine
# (piper, its package faked below) -- free, keyless, and in every environment.
SETTINGS = tas._settings(TTS_CHAIN="local/piper")
PARTS = ("concepts", "bible", "style", "cast", "places_proposal", "places", "season", "knowledge", "episode")
LABELS = ("concept", "bible", "style", "cast", "places proposal", "places", "season", "knowledge", "episode 1")
CONTINUE = "Continue the agent run: it picks up here and repeats nothing already done."


def fake_piper(text, voice, out_path, request, on_log):
    """A local engine's WAV: one 24 kHz mono second per ten characters (at least one)."""
    import wave

    with wave.open(out_path, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24000)
        wav.writeframes(b"\x00\x10" * (24000 * max(1, len(text) // 10)))


@pytest.fixture(autouse=True)
def local_speech(monkeypatch):
    """piper counts as installed and speaks through ``fake_piper``: the cast proposes its voices from the
    catalogue and the run measures them (``LOCAL_TTS`` is the real adapter, only the engine is faked)."""
    from clipping.aistory import voices
    from clipping.providers import tts

    monkeypatch.setattr(voices, "_installed", lambda name: True)
    monkeypatch.setattr(tts, "_installed", lambda name: True)
    monkeypatch.setitem(tts._LOCAL_SYNTH, "piper", fake_piper)


def _agent():
    from clipping.aistory.steps import story_fast_track

    return story_fast_track


def _wf():
    from clipping.aistory import workflow

    return workflow


# ------------------------------------------------------------------ the replies

CONCEPT = {
    "title": "Le coco sonne deux fois",
    "logline": "Sur une île de téléréalité, des fruits en couple mentent pour survivre au vote.",
    "world": "Une île tropicale filmée jour et nuit où chaque semaine un couple est éliminé.",
    "cast_sketch": [
        {"name": "Kiwilo", "role": "lead", "one_line": "Un kiwi charmeur qui joue pour gagner."},
        {"name": "Mangella", "role": "lead", "one_line": "Une mangue glaciale qui ne perd jamais."},
        {"name": "Broccolia", "role": "recurring", "one_line": "L'animatrice qui en sait plus que tous."},
    ],
    "hook_formula": "Le téléphone en noix de coco sonne dans la première seconde.",
    "value": "La loyauté contre l'ambition.",
    "retention_mechanics": "Un vote chaque semaine.",
    "style_fit": "fruit_drama",
}
C1_REPLY = {"concepts": [CONCEPT]}

K1 = {
    "Kiwilo": tcs.K1_KIWI,
    "Mangella": tcs.K1_MANGO,
    "Broccolia": tcs.k1("an anthropomorphic broccoli with dense dark green florets and a velvet gown",
                        ["rhinestone headband microphone", "emerald velvet gown"], gender="female",
                        tags=("authoritative",), sample="Le vote est ouvert, mes chéris."),
}


def k1_reply(call):
    name = call["user"].split("Character to write: ", 1)[1].split(" (", 1)[0]
    return copy.deepcopy(K1[name])


P0_REPLY = {
    "places": [{"name": "Le Parloir des Secrets", "one_line": "Là où chacun avoue tout à la caméra."},
               {"name": "La Piscine de la Trahison", "one_line": "Là où les alliances se font et se défont."}],
    "props": [{"name": "Téléphone en noix de coco", "one_line": "Il annonce le résultat du vote.",
               "owner": None}],
}
P1 = {
    "Le Parloir des Secrets": {"descriptor": "a dimly lit tropical wooden confession booth with a bamboo chair",
                               "layout_notes": "a stool at the center, a hidden camera slit in the right wall",
                               "time_variants": ["day"]},
    "La Piscine de la Trahison": {"descriptor": "a turquoise swimming pool ringed by white wooden loungers",
                                  "layout_notes": "the water in front, a tiki bar on the right",
                                  "time_variants": ["day"]},
}
R1_REPLY = {"descriptor": "a polished half coconut shell shaped like a vintage telephone", "owner": None}


def p1_reply(call):
    name = call["user"].split("Place to write: ", 1)[1].split("\n", 1)[0]
    return copy.deepcopy(P1[name])


S1_REPLY = {"arc": [{"ep": ep, "function": eps.ARC_FUNCTIONS[ep - 1],
                     "summary": f"Épisode {ep} : les alliances de l'île tremblent."} for ep in range(1, 9)]}


def s2_reply(call):
    return tcs.s2("Kiwilo et Mangella s'allient, puis le téléphone sonne.", ("Kiwilo", "Mangella"))


# E1 of the episode fixture with every scene by day: the agent's places are drawn by day only.
E1_REPLY = copy.deepcopy(eps.E1_REPLY)
for _scene in E1_REPLY["scenes"]:
    _scene["time_variant"] = "day"

PRE_PRODUCTION_PROMPTS = (["C1", "B1", "B2", "B3"] + ["K1"] * 3 + ["P0", "P1", "P1", "R1", "S1"] + ["S2"] * 8)
EPISODE_PROMPTS = tft.SCRIPT_PROMPTS + ["T1"] * tft.SCENES + ["M1"] * 3


def llm(**overrides):
    queues = {"C1": [C1_REPLY], "B1": [tss.B1_REPLY], "B2": [tss.B2_REPLY], "B3": [tss.B3_REPLY],
              "P0": [P0_REPLY], "S1": [S1_REPLY], "E1": [E1_REPLY], "E2": [tft.E2_OK] * len(eps.BODY),
              "E3": [eps.E3_FULL], "E4": [eps.E4_PASSED]}
    queues.update(overrides)
    return eps.FakeLLM(default={"K1": k1_reply, "P1": p1_reply, "R1": R1_REPLY, "S2": s2_reply, "T1": eps.t1_reply,
                                "M1": tms.m1_reply}, **queues)


class Failing:
    """*inner*, but the *nth* call of *prompt* (counting only those whose
    user text holds *match*, when given) fails as an outage of every link;
    the failed call never reaches *inner*."""

    def __init__(self, inner, prompt, *, match=None, nth=1):
        self.inner, self.prompt, self.match, self.nth = inner, prompt, match, nth
        self.seen = 0
        self.failed = []
        self._ids = {name: prompt_id for prompt_id, name in prompts.SCHEMA_NAMES.items()}

    def __call__(self, chain, **kwargs):
        prompt = self._ids[kwargs["schema_name"]]
        if prompt == self.prompt and (self.match is None or self.match in kwargs["user"]):
            self.seen += 1
            if self.seen == self.nth:
                self.failed.append(prompt)
                raise ProviderError("x", failures=[("gemini/gemini-test", "APITimeoutError: timed out")])
        return self.inner(chain, **kwargs)

    def prompts(self):
        return self.inner.prompts()


# ------------------------------------------------------------------ the story

def _story(store, *, mode="agent", seed=SEED, **profile):
    """A French agent story on the free chain, prompt-only, on fruit_drama.

    Plan 22 stage 2 (DEC-274): ``store.create`` now stamps every new story
    "writing": "v3", which (with this fixture's own non-empty *seed*) would
    switch C1 to C1v2 and add a C1J judge call -- this file tests the
    fast-track orchestration end to end, not concept-fidelity, and its whole
    ``FakeLLM`` queue is built for C1's reply; pinned to "v2" here unless a
    test overrides it, so every existing assertion (``C1``'s prompt, its
    reply queue depth) stays exactly as it was.
    """
    generation_profile = {"consistency_mode": "prompt_only", "writing": "v2", **profile}
    if mode is not None:
        generation_profile["mode"] = mode
    return store.create(language="fr", seed_text=seed, style_template_id="fruit_drama",
                        generation_profile=generation_profile, now=NOW)["story_id"]


def _local():
    from clipping.providers import tts

    return tts.LOCAL_TTS


def _fakes(tmp_path, runner=None, *, image=None):
    return tft.Fakes(tmp_path, runner=llm() if runner is None else runner,
                     image=tas.FakeImage() if image is None else image, local=_local())


def _ctx(store, story_id, *, params=None, settings=None):
    ctx, log = eps._ctx(store, story_id, step="story-fast-track", ep=None, params=params,
                        settings=SETTINGS if settings is None else settings)
    seen = []
    ctx.on_sub_step = seen.append
    return ctx, log, seen


def run(store, story_id, fakes, *, params=None, settings=None):
    """``(summary, log, sub_steps)``."""
    ctx, log, seen = _ctx(store, story_id, params=params, settings=settings)
    return _agent().run(ctx, **fakes.kwargs()), log, seen


def stopped(store, story_id, fakes, **kwargs):
    """``(message, log, sub_steps)`` of a run that stops."""
    ctx, log, seen = _ctx(store, story_id, params=kwargs.get("params"), settings=kwargs.get("settings"))
    with pytest.raises(steps.StepFailed) as caught:
        _agent().run(ctx, **fakes.kwargs())
    return " ".join(str(caught.value).split()), log, seen


def _images(fakes):
    return [request.extra.get("name") for request in fakes.image.requests]


def _entities(store, story_id):
    return {kind: store.list_entities(story_id, kind) for kind in ("characters", "places", "props")}


KIWILO, MANGELLA, BROCCOLIA = eps.KIWILO, eps.MANGELLA, eps.BROCCOLIA
PARLOIR, PISCINE, PHONE = eps.PARLOIR, eps.PISCINE, eps.PHONE


# ============================================================ the whole chain

def test_one_run_takes_an_agent_story_from_its_seed_to_episode_1_rendered(store, tmp_path):
    wf = _wf()
    story_id = _story(store)
    fakes = _fakes(tmp_path)

    summary, log, seen = run(store, story_id, fakes)

    # Every part in order, each a feed line and the job's sub-step.
    assert seen == list(PARTS)
    for number, label in enumerate(LABELS, start=1):
        assert f"⏩ Agent {number}/9: {label}" in log
    # The calls: one concept from the seed, the bible, the cast of the sketch, the places, the season,
    # then the fast track's own; the images: the preview strip, 3 portraits + 6 sheets, 2 plates, 1 prop.
    assert fakes.runner.prompts() == PRE_PRODUCTION_PROMPTS + EPISODE_PROMPTS
    assert SEED in fakes.runner.of("C1")[0]["user"] and "(call 1 of 1)" in fakes.runner.of("C1")[0]["user"]
    names = _images(fakes)
    assert names[:3] == ["preview_1", "preview_2", "preview_3"]
    assert names.count("portrait") == 3 and names.count("variant_day") == 2 and names.count("image") == 1
    # The story: ready, every approval given by the agent run and recorded so (RC-G1).
    story = store.get(story_id)
    assert story["status"] == "ready" and story["generation_profile"]["mode"] == "agent"
    assert story["approved_by"] == {"concept": "agent", "bible": "agent", "style": "agent", "season": "agent"}
    for key in ("concept", "bible", "style", "season"):
        assert wf.approved_by(story, key) == "agent"
    entities = _entities(store, story_id)
    assert [doc["char_id"] for doc in entities["characters"]] and {doc["char_id"] for doc in entities[
        "characters"]} == {KIWILO, MANGELLA, BROCCOLIA}
    assert {doc["place_id"] for doc in entities["places"]} == {PARLOIR, PISCINE}
    assert [doc["prop_id"] for doc in entities["props"]] == [PHONE]
    for docs in entities.values():
        for doc in docs:
            assert doc["approved_at"] and doc["approved_by"] == "agent" and wf.approved_by(doc) == "agent"
    assert all(doc["voice"] for doc in entities["characters"])  # cast.run pins the voices itself
    season = store.read_doc(story_id, "season.json")
    assert season["episodes_planned"] == 8 and season["approved_by"] == "agent"
    assert store.read_doc(story_id, "style_lock.json")["locked_at"]
    assert len(store.read_doc(story_id, "concepts.json")["concepts"]) == 1
    # Episode 1: made by the fast track, its documents approved by its own rules (by: fast_track).
    script = tft._doc(store, story_id, "script.json")
    assert script["approved_at"] and script.get("approved_by") == "fast_track"
    assert tft._doc(store, story_id, "assets.json")["approved"]["by"] == "fast_track"
    assert (tft._ep_dir(store, story_id) / "episode_final.mp4").is_file()
    assert list(tft._doc(store, story_id, "metadata_pack.json")["platforms"]) == list(schemas.PLATFORMS)
    assert summary["episode"]["steps"]["render"]["state"] == "completed"
    assert summary["episode"]["auto_approved"] == ["script", "storyboard", "assets"]
    assert summary["approved"] == [
        "the concept", "the bible", "the style", "character Kiwilo", "character Mangella", "character Broccolia",
        "place Le Parloir des Secrets", "place La Piscine de la Trahison", "prop Téléphone en noix de coco",
        "the season"]
    assert any(line.startswith("⚠️ Agent mode approves the style, the cast and the places as soon as they are "
                               "complete -- no taste check") for line in log)
    assert log[-1].startswith("🏁 Agent run done: episode 1 is rendered with its metadata pack")
    # The estimate now: nothing left to do.
    estimate = wf.story_fast_track_estimate(store, store.get(story_id), env=SETTINGS)
    assert all(row["kept"] for row in estimate["parts"]) and estimate["ready"] is True
    assert estimate["message"] == "Nothing left to do: episode 1 is rendered with its metadata pack."


def test_a_second_run_after_a_full_one_repeats_nothing(store, tmp_path):
    story_id = _story(store)
    run(store, story_id, _fakes(tmp_path))
    before = store.get(story_id)
    final = (tft._ep_dir(store, story_id) / "episode_final.mp4").read_bytes()

    again = tft.Fakes(tmp_path, runner=tft.no_llm(), image=tas.NeverImage(), local=_local())
    summary, log, seen = run(store, story_id, again)

    assert again.runner.calls == [] and again.image.requests == [] and again.edge.calls == []
    assert again.ffmpeg.calls == [] and again.cover.calls == []
    assert store.get(story_id)["approvals"] == before["approvals"]
    assert (tft._ep_dir(store, story_id) / "episode_final.mp4").read_bytes() == final
    assert summary["approved"] == [] and seen == list(PARTS)
    assert all(summary["parts"][name]["kept"] for name in PARTS[:-1])
    assert "📄 The bible is approved already: kept as it is." in log


# ================================================================ idempotence

PREVIEWS = {"preview_1", "preview_2", "preview_3"}


@pytest.mark.parametrize("part, prompt, match, nth, fail_images, starts", [
    ("concepts", "C1", None, 1, (), "No concept was generated: every C1 call failed"),
    ("bible", "B2", None, 1, (), "Bible incomplete: B2 failed"),
    ("style", None, None, 1, PREVIEWS, "No preview image was made on route auto"),
    ("cast", "K1", "Character to write: Mangella", 1, (), "Cast incomplete: Mangella text failed"),
    ("places_proposal", "P0", None, 1, (), "P0:"),
    ("places", "P1", "Place to write: La Piscine", 1, (), "Places incomplete"),
    ("season", "S2", None, 3, (), "Season arc incomplete: episode 3 failed"),
    ("episode", "E1", None, 1, (), "Fast track stopped at the script (step 1 of 6):"),
])
def test_a_stop_at_each_part_then_a_run_again_repeats_nothing(store, tmp_path, part, prompt, match, nth,
                                                              fail_images, starts):
    """The two runs together make exactly the calls of one full run: every
    prompt and every image counted, the failed ones aside."""
    story_id = _story(store)
    runner = llm() if prompt is None else Failing(llm(), prompt, match=match, nth=nth)
    first = _fakes(tmp_path, runner, image=tas.FakeImage(fail_for=fail_images))
    number = PARTS.index(part) + 1

    message, log, seen = stopped(store, story_id, first)

    label = LABELS[number - 1]
    where = label if part == "episode" else f"the {label}"
    assert message.startswith(f"Agent run stopped at {where} ({number} of 9): {starts}"), message
    assert message.endswith(CONTINUE) and "Continue the fast track" not in message
    assert seen == list(PARTS[:number])
    # Everything before the stop is approved, and kept by the next run.
    again = _fakes(tmp_path)
    summary, log, _seen = run(store, story_id, again)
    assert summary["episode"]["steps"]["render"]["state"] == "completed"
    for name in PARTS[:number - 1]:
        assert summary["parts"][name]["kept"] is True, name
    if prompt is not None:
        assert runner.failed == [prompt]
    made = collections.Counter(first.runner.prompts()) + collections.Counter(again.runner.prompts())
    assert made == collections.Counter(PRE_PRODUCTION_PROMPTS + EPISODE_PROMPTS)
    full = collections.Counter(_images(_full_run(tmp_path / "full")))
    images = collections.Counter(_images(first)) - collections.Counter(set(fail_images)) + collections.Counter(
        _images(again))
    assert images == full


def _full_run(tmp_path):
    """The image requests of one full run, on a store of its own."""
    tmp_path.mkdir()
    other = eps.StoryStore(tmp_path / "outputs", on_log=lambda line: None)
    fakes = _fakes(tmp_path)
    run(other, _story(other), fakes)
    return fakes


# ===================================================== a v2 story's knowledge base

D4 = {"geography": "Le parloir des secrets domine la piscine, de l'autre côté du jardin.",
      "period_details": "Une téléréalité tropicale d'aujourd'hui, caméras partout.",
      "visual_motifs": ["des noix de coco fêlées", "des torches au crépuscule"]}
KEY_NAME, KEY_ID = "Clé dorée", "prop_cle_doree"


def d5_reply(call):
    ep = next(ep for ep in range(1, 13) if f"Plan episode {ep}'s beats" in call["user"])
    objects = ["Téléphone en noix de coco"] + ([KEY_NAME] if ep == 1 else [])
    return {"beats": [
        {"what": f"Épisode {ep} : Kiwilo cache le téléphone sous un coussin.", "place": "Le Parloir des Secrets",
         "who": ["Kiwilo", "Mangella"], "objects": objects,
         "knows_after": [{"who": "Mangella", "knows": "Kiwilo cache quelque chose."}]},
    ]}


D6_KEY = {"keep": ["Téléphone en noix de coco"],
          "new_props": [{"name": KEY_NAME, "one_line": "La clé du coffre des votes.", "owner": "Kiwilo"}]}
PLACE_LOOK = {"layout_map": {"left": "a bamboo chair", "right": "a hidden camera slit", "back": "a palm wall",
                             "foreground": "", "centre": "a rustic stool"},
              "scale_note": "a small booth, two people wide", "lighting": {"day": "warm light through slats"},
              "props_here": [PHONE]}
R1_KEY = {"descriptor": "a small ornate golden key with a coconut-shaped bow", "owner": "Kiwilo"}


def r1v2_reply(call):
    return {"scale_cm": 8, "material": "polished gold", "colour": "gold", "scale_phrase": "as big as a finger",
            "where_when": [{"ep": 1, "holder": "Kiwilo", "place": "Le Parloir des Secrets", "note": "il la cache"}]}


# A v2 story draws its props on the quality links (the prop role: fal's seedream), paid.
QUALITY = dict(SETTINGS, **tas.FAL, ALLOW_PAID="1", PER_EPISODE_CAP_USD="10", DAILY_CAP_USD="50",
               PER_STORY_CAP_USD="50")


def _v2_story_at_the_knowledge(store):
    """The episode fixture's v2 story on the quality budget profile, ready
    but for its knowledge base, in agent mode; its places and props carry
    their looks (the knowledge's new prop is the only thing the places step
    has to make)."""
    story_id = eps._ready_story(store, v2=True, knowledge=False)
    store.update(story_id, lambda doc: doc["generation_profile"].update(mode="agent", budget_profile="quality"),
                 now=NOW)
    for place_id in (PARLOIR, PISCINE):
        doc = store.read_entity(story_id, "places", place_id)
        doc["look"] = copy.deepcopy(PLACE_LOOK)
        store.write_entity(story_id, "places", doc, now=NOW)
    doc = store.read_entity(story_id, "props", PHONE)
    doc["look"] = {"scale_cm": 20, "material": "polished coconut shell", "colour": "brown",
                   "scale_phrase": "as big as a hand",
                   "where_when": [{"ep": 1, "holder_char_id": None, "place_id": PARLOIR, "note": "il sonne"}]}
    store.write_entity(story_id, "props", doc, now=NOW)
    assert store.get(story_id)["status"] == "ready"
    return story_id


def test_a_v2_story_s_knowledge_base_is_written_approved_and_its_new_prop_drawn(store, tmp_path):
    wf = _wf()
    story_id = _v2_story_at_the_knowledge(store)
    inner = eps.FakeLLM(D4=[D4], D6=[D6_KEY], R1=[R1_KEY], default={"D5": d5_reply, "R1v2": r1v2_reply})
    fal = tas.FakeImage(price=0.04)
    fakes = tft.Fakes(tmp_path, runner=Failing(inner, "E1v2"), image=tas.NeverImage(), fal=fal, local=_local())

    estimate = wf.story_fast_track_estimate(store, store.get(story_id), env=QUALITY)
    message, log, seen = stopped(store, story_id, fakes, settings=QUALITY)

    # Stopped at episode 1's script (its own E1 outage), after the knowledge base and the new prop.
    assert message.startswith("Agent run stopped at episode 1 (9 of 9): Fast track stopped at the script"), message
    assert inner.prompts() == ["D4"] + ["D5"] * 8 + ["D6", "R1", "R1v2"]
    doc = store.read_knowledge(story_id)
    assert doc["approved_at"] and doc["approved_rev"] == doc["rev"] and doc["approved_by"] == "agent"
    assert wf.knowledge_current(doc) and wf.approved_by(doc) == "agent"
    key = store.read_entity(story_id, "props", KEY_ID)
    assert key["approved_by"] == "agent" and key["image"] and key["look"]
    story = store.get(story_id)
    assert story["status"] == "ready" and story["approvals"]["places"]
    # The parts before were kept as they were: the human's approvals stay the human's.
    assert "approved_by" not in story
    assert "🗺 The knowledge base added props: the places step draws them now." in log
    # The key's image -- and, the fixture's documents naming images that are not on disk, its two plates
    # and the phone's image: the places step makes whatever is missing, on the prop and plate role's link.
    assert sorted(request.extra["name"] for request in fal.requests) == ["image", "image", "variant_day",
                                                                          "variant_day"]
    # The estimate counted the props D6 may add (up to three, written and drawn), paid, before any call.
    knowledge = estimate["parts"][7]
    assert knowledge["units"]["images"] == schemas.D6_NEW_PROPS_MAX and knowledge["paid"] is True
    assert knowledge["exact"] is False and "knowledge" in estimate["paid"]
    assert seen == list(PARTS)


# ============================================================ the concept: count

def _concepts_run(store, story_id, params, runner):
    from clipping.aistory.steps import concepts

    ctx, log = eps._ctx(store, story_id, step="concepts", ep=None, params=params, settings=SETTINGS)
    return concepts.run(ctx, runner=runner, time_fn=eps.Clock(0.0)), log


def test_the_concepts_step_writes_count_concepts_and_still_ten_by_default(store):
    from clipping.aistory.steps import concepts

    story_id = _story(store, mode=None)
    runner = eps.FakeLLM(C1=[C1_REPLY])

    summary, log = _concepts_run(store, story_id, {"count": 1}, runner)

    assert summary == {"generated": 1, "concept_ids": ["gen_01"], "failed_calls": []}
    assert runner.prompts() == ["C1"] and "(call 1 of 1)" in runner.of("C1")[0]["user"]
    assert "Generated 1 of 1 concepts." in log
    assert concepts.read_count({}) == concepts.read_count(None) == concepts.read_count({"count": None}) == 10
    assert concepts.PARAMS == ("count",)
    for bad in (0, 11, True, "3", 2.0):
        with pytest.raises(steps.StepFailed) as caught:
            _concepts_run(store, story_id, {"count": bad}, eps.FakeLLM())
        assert str(caught.value) == (f"The concepts step's count is the number of concepts to write, 1 to 10, "
                                     f"not {bad!r}.")
    with pytest.raises(steps.StepFailed) as caught:
        _concepts_run(store, story_id, {"n": 1}, eps.FakeLLM())
    assert str(caught.value) == "The concepts step takes only count; not 'n'."


# ============================================================ the cast pick

def _with_sketch(store, names, *, existing=()):
    story_id = _story(store)
    sketch = [{"name": name, "role": "lead" if i < 2 else "support", "one_line": f"{name} joue pour gagner."}
              for i, name in enumerate(names)]
    store.update(story_id, lambda doc: doc.update(concept=dict(CONCEPT, cast_sketch=sketch)), now=NOW)
    for doc in eps.CHARACTERS:
        if doc["name"] in existing:
            store.write_entity(story_id, "characters", copy.deepcopy(doc), now=NOW)
    return store.get(story_id)


def test_the_cast_is_the_concept_s_sketch_up_to_five_then_the_cast_cap(store, monkeypatch):
    wf = _wf()
    seven = ["Ananas", "Banane", "Cerise", "Datte", "Figue", "Goyave", "Kaki"]
    assert wf.agent_cast_pick(store, _with_sketch(store, seven)) == seven[:5]
    assert wf.agent_cast_pick(store, _with_sketch(store, ["Ananas", "Banane"])) == ["Ananas", "Banane"]
    assert _agent().CAST_PICK_MAX == 5 and wf.MAX_CAST == 8
    # A name the story already has is included at no cost; new ones only while MAX_CAST leaves room.
    monkeypatch.setattr(wf, "MAX_CAST", 4)
    story = _with_sketch(store, ["Kiwilo", "Ananas", "Mangella", "Banane", "Cerise"],
                         existing=("Kiwilo", "Mangella", "Broccolia"))
    assert wf.agent_cast_pick(store, story) == ["Kiwilo", "Ananas", "Mangella"]


def test_a_concept_with_no_cast_sketch_stops_the_run_at_the_cast_before_anything(store, tmp_path):
    story = _with_sketch(store, [])
    story_id = story["story_id"]
    store.update(story_id, lambda doc: doc["approvals"].update(concept=NOW, bible=NOW, style=NOW), now=NOW)
    fakes = _fakes(tmp_path)

    message, _log, seen = stopped(store, story_id, fakes)

    assert message == ("Agent run stopped at the cast (4 of 9): Pick characters from the concept's cast sketch or "
                       f"add your own first. {CONTINUE}")
    assert seen == [] and fakes.runner.calls == [] and fakes.image.requests == []


# ============================================================ who approved (RC-G1)

def test_every_story_approval_of_the_run_goes_through_the_workflow_by_agent(store, tmp_path, monkeypatch):
    """RC-G1: the agent run writes the story's approvals only through
    ``workflow.approve_*`` (and the concept's choice), each ``by: agent``;
    episode 1's documents only through the fast track's own approvals."""
    wf = _wf()
    calls = []
    for name in ("choose_concept", "approve_bible", "approve_style", "approve_entity", "approve_season",
                 "approve_knowledge", "approve_script", "approve_storyboard", "approve_assets",
                 "approve_keyframes"):
        real = getattr(wf, name)

        def wrapped(*args, _real=real, _name=name, **kwargs):
            calls.append((_name, kwargs.get("by")))
            return _real(*args, **kwargs)

        monkeypatch.setattr(wf, name, wrapped)
    story_id = _story(store)

    run(store, story_id, _fakes(tmp_path))

    story_calls = [call for call in calls if call[0] not in ("approve_script", "approve_storyboard",
                                                            "approve_assets")]
    assert story_calls == ([("choose_concept", "agent"), ("approve_bible", "agent"), ("approve_style", "agent")]
                           + [("approve_entity", "agent")] * 6 + [("approve_season", "agent")])
    assert [call for call in calls if call not in story_calls] == [
        ("approve_script", "fast_track"), ("approve_storyboard", None), ("approve_assets", "fast_track")]


def test_a_human_approval_takes_the_agent_s_mark_off_and_a_studio_story_never_gains_one(store):
    wf = _wf()
    story_id = _story(store)
    wf.choose_concept(store, story_id, concept=CONCEPT, now=NOW, by=wf.AGENT_APPROVED)
    store.update(story_id, lambda doc: doc.update(tss.B1_REPLY, world=tss.B2_REPLY, **tss.B3_REPLY), now=NOW)
    wf.approve_bible(store, story_id, now=NOW, by=wf.AGENT_APPROVED)
    story = store.get(story_id)
    assert story["approved_by"] == {"concept": "agent", "bible": "agent"}
    assert (wf.approved_by(story, "bible"), wf.approved_by(story, "style")) == ("agent", None)

    story = wf.approve_bible(store, story_id, now=NOW)
    assert story["approved_by"] == {"concept": "agent"} and wf.approved_by(story, "bible") == "user"
    story = wf.choose_concept(store, story_id, concept=CONCEPT, now=NOW)
    assert "approved_by" not in story
    with pytest.raises(ValueError):
        wf.approve_bible(store, story_id, now=NOW, by="fast_track")

    studio = _story(store, mode=None)
    wf.choose_concept(store, studio, concept=CONCEPT, now=NOW)
    assert "approved_by" not in store.get(studio) and "mode" not in store.get(studio)["generation_profile"]


def test_a_studio_story_is_refused_before_anything(store, tmp_path):
    story_id = _story(store, mode=None)
    fakes = _fakes(tmp_path)

    message, _log, seen = stopped(store, story_id, fakes)

    assert message.startswith("The agent run is for a story in agent mode: this story is in Studio mode")
    assert seen == [] and fakes.runner.calls == [] and store.read_doc(story_id, "concepts.json") is None


# ============================================================ the estimate

def test_the_estimate_sums_every_part_still_to_do(store):
    wf = _wf()
    story_id = _story(store)

    body = wf.story_fast_track_estimate(store, store.get(story_id), env=SETTINGS)

    rows = {row["part"]: row for row in body["parts"]}
    assert [row["part"] for row in body["parts"]] == list(PARTS)
    assert [row["number"] for row in body["parts"]] == list(range(1, 10))
    calls = {name: row["units"]["llm_calls"] for name, row in rows.items()}
    assert calls == {"concepts": 1, "bible": 3, "style": 0, "cast": 5, "places_proposal": 1, "places": 6,
                     "season": 9, "knowledge": 0, "episode": 1 + len(eps.BODY) + 2 + tft.SCENES + 3}
    images = {name: row["units"]["images"] for name, row in rows.items()}
    # The preview strip; up to five characters (a portrait and two sheets each, text to image when
    # prompt-only); P0's most (3 places and 3 props).
    assert images == {"concepts": 0, "bible": 0, "style": 3, "cast": 15, "places_proposal": 0, "places": 6,
                      "season": 0, "knowledge": 0, "episode": 0}
    assert rows["knowledge"]["kept"] is True  # a legacy story has none
    assert [rows[name]["exact"] for name in ("cast", "places", "episode")] == [False, False, False]
    assert body["llm_calls"] == sum(calls.values()) and body["est_usd"] == 0.0 and body["paid"] == []
    assert body["exact"] is False and body["ready"] is True and body["stops_at"] is None
    ft = _agent().fast_track_step
    # A quarter of an hour a part of pre-production still to do (seven: a legacy story has no knowledge
    # base), the fast track's free-chain hour for episode 1.
    assert body["budget"]["seconds"] == 7 * 15 * 60 + ft.FAST_TRACK_BUDGET_SECONDS
    assert body["budget"]["ceiling_seconds"] == ft.FAST_TRACK_BUDGET_CEILING_SECONDS + 8 * 15 * 60
    assert body["message"].startswith("8 parts to do (concept, bible, style, cast, places proposal, places, "
                                      "season and episode 1): ")
    assert "nothing paid" in body["message"]
    # A Studio story has none.
    with pytest.raises(wf.WorkflowError) as refused:
        wf.story_fast_track_estimate(store, store.get(_story(store, mode=None)), env=SETTINGS)
    assert refused.value.code == wf.CONFLICT


def test_the_premium_writing_cost_is_folded_into_the_sum_and_the_cap(store):
    """Plan 22 stage 1, amended on review: the premium chain's own cost is a
    real spend, so it is folded into est_usd and the cap check like any
    paid image -- a story whose images are all free but whose writing is
    premium must still be refused once that alone goes over a cap."""
    wf = _wf()
    story_id = _story(store)
    premium = dict(SETTINGS, STORY_LLM_PREMIUM_CHAIN="gemini-paid/gemini-3.8-flash",
                   GEMINI_PAID_API_KEY="test-paid-key", ALLOW_PAID="1")

    body = wf.story_fast_track_estimate(store, store.get(story_id), env=premium)

    usd = body["text_usd"]["usd"]
    assert usd > 0 and body["text_usd"]["calls"] > 0
    pending_sum = round(sum(row["est_usd"] for row in body["parts"] if not row["kept"]) + usd, 4)
    assert body["est_usd"] == pending_sum
    assert body["ready"] is True
    assert f"incl. ${usd:.2f} writing" in body["message"]
    assert "writing (est $" in body["message"]

    # The images stay free in this story (SETTINGS), so a cap too small for
    # the writing cost alone is enough to refuse the whole run.
    tiny = dict(premium, PER_STORY_CAP_USD=f"{usd / 2:.4f}")
    stopped_body = wf.story_fast_track_estimate(store, store.get(story_id), env=tiny)

    assert stopped_body["ready"] is False
    reason = stopped_body["stops_at"]["reason"]
    assert "writing (est $" in reason and "this story to $" in reason and "of its" in reason


def test_the_estimate_refuses_at_the_first_part_that_cannot_run(store):
    wf = _wf()
    story_id = _story(store)
    keyless = dict(SETTINGS)
    del keyless["GOOGLE_API_KEY"]

    body = wf.story_fast_track_estimate(store, store.get(story_id), env=keyless)

    assert body["ready"] is False and body["stops_at"]["part"] == "concepts" and body["stops_at"]["number"] == 1
    assert body["stops_at"]["reason"].startswith("No link in the LLM chain has an API key")
    assert body["message"] == body["stops_at"]["reason"]


# ============================================================ RC-A3

PAID = dict(SETTINGS, **tas.PAID_IMAGES)


def test_paid_parts_while_allow_paid_is_off_are_refused_and_named_before_anything(store, tmp_path):
    wf = _wf()
    story_id = _story(store)
    fakes = _fakes(tmp_path)

    body = wf.story_fast_track_estimate(store, store.get(story_id), env=PAID)
    message, _log, seen = stopped(store, story_id, fakes, settings=PAID)

    assert body["paid"] == ["style", "cast", "places"]
    assert body["stops_at"]["part"] == "style" and "allow_paid is off" in body["stops_at"]["reason"]
    assert message.startswith("Agent run stopped at the style (3 of 9): ") and "allow_paid is off" in message
    assert seen == [] and fakes.runner.calls == [] and fakes.image.requests == []
    assert store.read_doc(story_id, "concepts.json") is None and tft._ledger(store, story_id) == []


def test_paid_parts_over_a_cap_stop_the_run_before_anything_is_bought(store, tmp_path):
    wf = _wf()
    story_id = _story(store)
    allowed = dict(PAID, ALLOW_PAID="1")
    total = wf.story_fast_track_estimate(store, store.get(story_id), env=allowed)
    assert total["ready"] is True and total["paid"] == ["style", "cast", "places"] and total["est_usd"] > 0
    capped = dict(allowed, PER_STORY_CAP_USD=f"{total['est_usd'] / 2:.4f}")
    fakes = _fakes(tmp_path)

    body = wf.story_fast_track_estimate(store, store.get(story_id), env=capped)
    message, _log, seen = stopped(store, story_id, fakes, settings=capped)

    reason = body["stops_at"]["reason"]
    assert body["stops_at"]["part"] == "style"
    assert reason.startswith("The agent run would go over a cap -- style (est $")
    assert "cast (est up to $" in reason and "places (est up to $" in reason and "this story to $" in reason
    assert "Caps: " in reason and "Nothing was generated or spent" in reason
    assert message == f"Agent run stopped at the style (3 of 9): {reason} {CONTINUE}"
    assert seen == [] and fakes.runner.calls == [] and fakes.image.requests == []
    assert tft._ledger(store, story_id) == []


def test_episode_1_s_predicted_price_counts_and_is_refused_while_allow_paid_is_off(store, tmp_path):
    wf = _wf()
    story_id = _story(store, budget_profile="one_dollar")
    fakes = _fakes(tmp_path)

    body = wf.story_fast_track_estimate(store, store.get(story_id), env=SETTINGS)
    message, _log, _seen = stopped(store, story_id, fakes)

    episode = body["parts"][-1]
    assert episode["exact"] is False and episode["paid"] is True and episode["est_usd"] == 1.0
    assert body["paid"] == ["episode 1"] and body["est_usd"] == 1.0
    assert message.startswith("Agent run stopped at episode 1 (9 of 9): The agent run needs paid generation -- "
                              "episode 1 (est up to $1.000), est $1.000 in all -- and allow_paid is off.")
    assert fakes.runner.calls == [] and fakes.image.requests == []


# ============================================================ the API

def test_a_story_is_created_in_studio_mode_unless_agent_is_asked(api):
    studio = api.client.post("/api/stories", json={"language": "fr"}).json()
    assert "mode" not in studio["generation_profile"]
    agent = api.client.post("/api/stories", json={"language": "fr", "seed_text": SEED, "mode": "agent"}).json()
    assert agent["generation_profile"]["mode"] == "agent"
    assert agent["generation_profile"]["tier"] == defaults.default_generation_profile()["tier"]
    assert api.client.post("/api/stories", json={"language": "fr", "mode": "robot"}).status_code == 422
    # PATCH may move a story into agent mode (the profile's own check).
    patched = api.client.patch(f"/api/stories/{studio['story_id']}",
                               json={"generation_profile": {"mode": "agent"}}).json()
    assert patched["generation_profile"]["mode"] == "agent"
    refused = api.client.patch(f"/api/stories/{studio['story_id']}", json={"generation_profile": {"mode": "x"}})
    assert refused.status_code == 400


def test_the_agent_run_is_one_job_on_an_agent_story_and_refused_on_a_studio_one(api):
    studio = api.client.post("/api/stories", json={"language": "fr"}).json()["story_id"]
    agent = api.client.post("/api/stories", json={
        "language": "fr", "seed_text": SEED, "style_template_id": "fruit_drama",
        "generation_profile": {"consistency_mode": "prompt_only"}, "mode": "agent"}).json()
    story_id = agent["story_id"]

    refused = api.client.post(f"/api/stories/{studio}/steps/story-fast-track", json={})
    assert refused.status_code == 409 and "Studio mode" in refused.json()["detail"]
    assert api.client.get(f"/api/stories/{studio}/estimate/story-fast-track").status_code == 409
    bad = api.client.post(f"/api/stories/{story_id}/steps/story-fast-track", json={"params": {"x": 1}})
    assert bad.status_code == 400 and bad.json()["detail"] == "'story-fast-track' takes no parameters."
    with_ep = api.client.post(f"/api/stories/{story_id}/steps/story-fast-track", json={"ep": 1})
    assert with_ep.status_code == 400
    assert api.submitted == []

    estimate = api.client.get(f"/api/stories/{story_id}/estimate/story-fast-track")
    assert estimate.status_code == 200
    assert [row["part"] for row in estimate.json()["parts"]] == list(PARTS) and estimate.json()["ready"] is True

    response = api.client.post(f"/api/stories/{story_id}/steps/story-fast-track", json={})
    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["kind"], job["step"], job["ep"], job["params"], job["sub_step"]) == (
        "story_step", "story-fast-track", None, {}, None)
    assert api.submitted == [job["id"]]
    again = api.client.post(f"/api/stories/{story_id}/steps/story-fast-track", json={})
    assert again.status_code == 409  # one step of a story at a time


def test_the_agent_run_is_refused_before_a_job_with_the_estimate_s_stop(api):
    api.monkeypatch.setattr(api.worker, "_settings_env", dict(PAID))
    story_id = api.client.post("/api/stories", json={"language": "fr", "style_template_id": "fruit_drama",
                                                     "mode": "agent"}).json()["story_id"]

    response = api.client.post(f"/api/stories/{story_id}/steps/story-fast-track", json={})

    assert response.status_code == 409
    assert response.json()["detail"].startswith("Agent run stopped at the style (3 of 9): ")
    assert api.submitted == [] and api.jobs.list_jobs() == []


def test_the_concepts_count_is_taken_by_both_routes(api):
    story_id = api.client.post("/api/stories", json={"language": "fr"}).json()["story_id"]

    job = api.client.post(f"/api/stories/{story_id}/concepts/generate", json={"count": 1}).json()
    assert job["step"] == "concepts" and job["params"] == {"count": 1}
    for path, body in (("concepts/generate", {"count": 0}), ("steps/concepts", {"params": {"count": 11}}),
                       ("steps/concepts", {"params": {"n": 1}})):
        response = api.client.post(f"/api/stories/{story_id}/{path}", json=body)
        assert response.status_code == 400, (path, response.text)


def test_the_worker_records_the_part_the_agent_run_is_on(worker, job_store, monkeypatch):
    seen = []

    def fake_run(ctx):
        ctx.on_sub_step("cast")
        seen.append(job_store.get_job(ctx.job_id)["sub_step"])
        ctx.on_sub_step("season")

    _register(monkeypatch, "story-fast-track", fake_run)
    job_id = _step_job(job_store, step="story-fast-track", story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    job = job_store.get_job(job_id)
    assert seen == ["cast"] and job["sub_step"] == "season" and job["status"] == "completed"
    # Any other step job never gets one.
    other = _step_job(job_store, step="bible", story_id=worker.story_id)
    _register(monkeypatch, "bible", lambda ctx: None)
    worker._run_pipeline_sync(other, {}, CancelToken())
    assert job_store.get_job(other).get("sub_step") is None


# ================================================== plan 22 stage 5: paused for the user's clips

PAUSED = {"state": "awaiting_uploads", "count": 3, "missing": [{"shot_id": "sh02"}, {"shot_id": "sh04"},
                                                                {"shot_id": "sh07"}],
          "message": "Waiting for 3 clips — download the brief",
          "brief": "/api/stories/x/episodes/1/brief.zip"}


def test_the_agent_run_pauses_at_episode_1_for_the_users_clips_and_goes_on_when_run_again(store, tmp_path,
                                                                                           monkeypatch):
    """Fail-first. Episode 1's fast track waiting for the user's own clips
    (the manual link) pauses the run -- a result awaiting uploads, never a
    stop -- with the brief named; run again (an upload that leaves nothing
    missing starts it), it keeps every part and goes on from episode 1."""
    from clipping.aistory.steps import fast_track as fast_track_step

    agent = _agent()
    story_id = _story(store)
    calls = []

    def paused(ctx, **_kwargs):
        calls.append("paused")
        return {"ep": 1, "state": "awaiting_uploads", "uploads": dict(PAUSED), "paused_at": "assets", "steps": {}}

    monkeypatch.setattr(fast_track_step, "run", paused)
    summary, log, seen = run(store, story_id, _fakes(tmp_path))
    assert steps.awaiting_uploads(summary) and summary["uploads"]["count"] == 3 and summary["paused_at"] == "episode"
    assert log[-1].startswith("⏸ Agent run paused at episode 1 (9 of 9): Waiting for 3 clips — download the brief.")
    assert seen == list(PARTS) and store.get(story_id)["status"] == "ready"

    def done(ctx, **_kwargs):
        calls.append("done")
        return {"ep": 1, "steps": {}, "auto_approved": ["assets"], "seconds": 1.0}

    monkeypatch.setattr(fast_track_step, "run", done)
    again = tft.Fakes(tmp_path, runner=tft.no_llm(), image=tas.NeverImage(), local=_local())
    summary, log, _seen = run(store, story_id, again)
    assert not steps.awaiting_uploads(summary) and calls == ["paused", "done"]
    assert again.runner.calls == [] and again.image.requests == []
    assert all(summary["parts"][name]["kept"] for name in PARTS[:-1])
    assert agent.STEP == "story-fast-track"


def test_the_worker_ends_a_step_waiting_for_the_users_clips_awaiting_uploads(worker, job_store, monkeypatch):
    """The job ends ``awaiting_uploads`` with what it waits for -- finished
    for the worker (a cancel is refused), never failed, kept over a restart."""
    _register(monkeypatch, "story-fast-track", lambda ctx: {"ep": 1, "state": "awaiting_uploads",
                                                            "uploads": dict(PAUSED)})
    job_id = _step_job(job_store, step="story-fast-track", story_id=worker.story_id)

    worker._run_pipeline_sync(job_id, {}, CancelToken())

    job = job_store.get_job(job_id)
    assert job["status"] == "awaiting_uploads" and job["uploads"]["count"] == 3 and not job.get("error")
    assert "is paused: Waiting for 3 clips — download the brief" in job_store.last_event(job_id)["message"]
    assert job_store.request_cancel(job_id) == "terminal"
    assert job_id not in job_store.fail_stale_jobs()
    assert job_store.resume_step_job(job_id, "job_new") == "ok"
    assert job_store.get_job(job_id)["status"] == "completed" and job_store.get_job(job_id)["resumed_by"] == "job_new"
