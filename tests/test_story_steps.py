"""The first real story steps: concepts, bible, regenerate (AI Story phase 1,
stage 6; spec 3 steps 2-3, 4.1, 9.2).

Every step writes into a real ``StoryStore`` under ``tmp_path`` and talks to a
stand-in for ``llm.run_chain`` that records each call's keyword arguments and
answers from a queue -- except the tests that go through the real
``run_chain`` with a fake ``client_factory`` (the ``test_preflight.py``
pattern), which is how a keyless link is shown never to be contacted. No
network, no sleeping, stdlib + pytest only: this file runs in the CI
environment (DEC-012).

The step modules are imported inside each test, not at the top, so that
against the parent commit (where they did not exist) every behaviour group
fails on its own -- ``ModuleNotFoundError`` for the new modules, and the
registry tests with ``UnknownStep`` -- rather than the whole file failing to
collect.
"""

from __future__ import annotations

import copy
import functools
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping.aistory import context, prompts, schemas, steps, templates
from clipping.aistory.store import StoryStore
from clipping.cancel import Cancelled, CancelToken
from clipping.providers import llm, pacing, registry
from clipping.providers.errors import ProviderError
from clipping.providers.registry import Link

REPO_ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-26T10:00:00+00:00"
LINK = Link("gemini", "gemini-test")
# Test values only: the key never leaves the process (every client is fake).
SETTINGS = {"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key"}

TENTAFRUIT = next(c for c in templates.load_concepts() if c["concept_id"] == "tentafruit_island")
LIBRARY_TITLES_FR = [c["title"]["fr"] for c in templates.load_concepts()]


def _new():
    """The stage-6 modules (absent on the parent commit)."""
    from clipping.aistory.steps import bible, concepts, llm_call, regenerate

    return SimpleNamespace(bible=bible, concepts=concepts, llm_call=llm_call, regenerate=regenerate)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """No key or chain from the machine running the tests reaches a step."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.delenv("LLM_CHAIN", raising=False)
    monkeypatch.delenv("ALLOW_PAID", raising=False)  # it decides which links a step calls
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    yield
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()


@pytest.fixture
def story_store(tmp_path):
    return StoryStore(tmp_path, on_log=lambda line: None)


# ------------------------------------------------------------------ helpers

class Log(list):
    def __call__(self, line):
        self.append(str(line))


def _story(store, *, chosen=True, language="fr", style="fruit_drama", seed=None):
    """A draft story, with ``tentafruit_island`` chosen unless *chosen* is off."""
    story_id = store.create(language=language, seed_text=seed, style_template_id=style, now=NOW)["story_id"]
    if chosen:
        concept = templates.localize_concept(TENTAFRUIT, language)

        def choose(doc):
            doc["concept_id"] = TENTAFRUIT["concept_id"]
            doc["concept"] = concept
            doc["title"] = concept["title"]
            doc["approvals"]["concept"] = NOW

        store.update(story_id, choose, now=NOW)
    return story_id


def _ctx(store, story_id, *, step="concepts", params=None, token=None, settings_env=None):
    log = Log()
    ctx = steps.StepContext(
        job_id="job000000001",
        story_id=story_id,
        step=step,
        ep=None,
        params=params or {},
        cancel=token or CancelToken(),
        settings_env=dict(SETTINGS) if settings_env is None else settings_env,
        outputs_dir=store.outputs_dir,
        on_log=log,
    )
    return ctx, log


class FakeRunner:
    """Stands in for ``llm.run_chain``: records every call, answers from a
    queue. An entry is a reply, an exception to raise, or ``f(call)`` that
    returns either (to look at the disk, or cancel, mid-run)."""

    def __init__(self, *replies, link=LINK):
        self.queue = list(replies)
        self.calls = []
        self.link = link

    def __call__(self, chain, **kwargs):
        call = dict(kwargs, chain=list(chain))
        self.calls.append(call)
        if not self.queue:
            raise AssertionError(f"no reply queued for call {len(self.calls)}")
        reply = self.queue.pop(0)
        if callable(reply) and not isinstance(reply, BaseException):
            reply = reply(call)
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply), self.link


def _c1_concept(title, style_fit="fruit_drama"):
    return {
        "title": title,
        "logline": "Une ligne courte qui reste bien sous la limite de trente mots.",
        "world": "Une île de téléréalité où des fruits vivent en couple sous les caméras.",
        "cast_sketch": [
            {"name": "Mangue", "role": "lead", "one_line": "Elle veut gagner et ment pour rester."},
            {"name": "Kiwi", "role": "support", "one_line": "Il est loyal tant que cela l'arrange."},
            {"name": "Coco", "role": "recurring", "one_line": "Il anime le jeu et en sait plus que tous."},
        ],
        "hook_formula": "Un vote s'ouvre dans la première seconde.",
        "value": "La loyauté contre l'ambition.",
        "retention_mechanics": "Un vote chaque semaine.",
        "style_fit": style_fit,
    }


def c1_reply(call, offset=0):
    """Call *call*'s one concept: "Titre <offset + call>", every other one anime."""
    return {"concepts": [_c1_concept(f"Titre {offset + call}", "anime" if call % 2 == 0 else "fruit_drama")]}


INVALID_C1 = {"concepts": [_c1_concept("Un"), _c1_concept("Deux")]}

B1_REPLY = {
    "logline": "Des fruits en couple survivent au vote hebdomadaire d'une île de téléréalité.",
    "premise": (
        "Chaque semaine, les couples de fruits affrontent le vote du public. Le "
        "téléphone-coco annonce les résultats. Tout le monde ment pour rester à l'écran."
    ),
    "tone": "mélodramatique, conscient de lui-même, rapide",
    "genre_tags": ["soap", "survie", "comédie"],
}
B2_REPLY = {
    "setting_summary": "Une île tropicale de téléréalité où des fruits vivent en couple sous les caméras.",
    "rules": [
        "Les fruits sont des personnes ; personne ne le commente.",
        "Un vote public élimine un couple chaque semaine.",
        "Le téléphone-coco annonce les résultats du vote.",
        "Rompre en public coûte son image à un candidat.",
    ],
    "time_period": "contemporain",
    "recurring_motifs": ["le téléphone-coco", "le feu d'élimination", "le miroir des coulisses"],
}
B3_REPLY = {
    "themes_and_values": ["la loyauté contre l'ambition", "ce qu'on fait pour être aimé"],
    "audience": {"age": "13+", "platforms": ["tiktok", "shorts"]},
    "why_come_back": [
        "Le vote dont tout le monde parle.",
        "Une alliance se brise à chaque épisode.",
        "Le téléphone-coco change toujours tout.",
    ],
}
# Three errors: premise has one sentence, one tag, a 16-word tone.
INVALID_B1 = dict(B1_REPLY, premise="Une seule phrase ici.", genre_tags=["soap"],
                  tone=" ".join(f"mot{i}" for i in range(16)))
INVALID_B2 = dict(B2_REPLY, rules=["Une seule règle."], recurring_motifs=["un motif"])

BIBLE_FIELDS = ("logline", "premise", "tone", "genre_tags", "world", "themes_and_values",
                "audience", "why_come_back")


def _avoid_titles(user):
    line = next(line for line in user.splitlines() if line.startswith("Do not repeat"))
    return line.split(": ", 1)[1].split(", ")


def _bible_written(store, m, **story_kwargs):
    story_id = _story(store, **story_kwargs)
    ctx, _ = _ctx(store, story_id, step="bible")
    m.bible.run(ctx, runner=FakeRunner(B1_REPLY, B2_REPLY, B3_REPLY))
    return story_id


def responder(contents):
    """A ``client_factory`` for the real ``run_chain``: records which links it
    built a client for and every request body, answers *contents* in order."""
    queue = list(contents)
    constructed = []
    seen = []

    class Completions:
        def __init__(self, provider):
            self.provider = provider

        def create(self, **kwargs):
            seen.append((self.provider, kwargs))
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=queue.pop(0)))],
                usage=SimpleNamespace(total_tokens=120),
            )

    def factory(link, **kwargs):
        constructed.append(link.provider)
        return SimpleNamespace(chat=SimpleNamespace(completions=Completions(link.provider)))

    factory.constructed = constructed
    factory.seen = seen
    return factory


def _no_sleep(seconds):
    raise AssertionError(f"the chain tried to sleep {seconds}s")


# ================================================================ concepts

def test_generate_ten_makes_ten_c1_calls_of_one_valid_card_each(story_store):
    m = _new()
    story_id = _story(story_store)
    ctx, log = _ctx(story_store, story_id)
    runner = FakeRunner(*(c1_reply(k) for k in range(1, 11)))

    summary = m.concepts.run(ctx, runner=runner)

    assert len(runner.calls) == 10
    for k, call in enumerate(runner.calls, 1):
        assert call["temperature"] == 0.9
        assert call["max_tokens"] == 700
        assert call["schema_name"] == "story_concepts"
        assert call["chain"] == [LINK]
        assert call["keys"] == {"gemini": "test-gemini-key"}
        assert call["cancel"] is ctx.cancel
        assert call["on_log"] is ctx.on_log
        assert f"(call {k} of 10)" in call["user"]
        assert "Invent exactly 1 original concept " in call["user"]
        assert "Visual style: Fruit Drama" in call["user"]
        assert call["system"].endswith("Write all user-facing text in French.")

    doc = story_store.read_doc(story_id, "concepts.json")
    assert schemas.story_concepts_errors(doc) == []
    ids = [card["concept_id"] for card in doc["concepts"]]
    assert ids == [f"gen_{n:02d}" for n in range(1, 11)]
    assert [card["title"] for card in doc["concepts"]] == [f"Titre {n}" for n in range(1, 11)]
    card = doc["concepts"][1]
    assert card["source"] == "generated"
    assert card["prompt_version"] == prompts.PROMPT_VERSION
    assert card["language"] == "fr"
    assert card["style_fit"] == "anime"
    assert card["cast_sketch"] == c1_reply(2)["concepts"][0]["cast_sketch"]
    # A generated card renders exactly like a library concept in a prompt.
    assert "Title: Titre 2" in context.concept_block(card)

    assert summary == {"generated": 10, "concept_ids": ids, "failed_calls": []}
    assert "💡 C1 call 1/10: Titre 1" in log
    assert "💡 C1 call 10/10: Titre 10" in log
    assert sum(line.startswith("✍️ C1 via gemini/gemini-test ≈") for line in log) == 10
    assert log[-1] == "Generated 10 of 10 concepts."
    assert not any(line.startswith("✂️") for line in log)


def test_a_second_run_continues_the_numbering_and_trims_the_avoid_list_once(story_store):
    m = _new()
    story_id = _story(story_store)
    m.concepts.run(_ctx(story_store, story_id)[0], runner=FakeRunner(*(c1_reply(k) for k in range(1, 11))))
    ctx, log = _ctx(story_store, story_id)

    summary = m.concepts.run(ctx, runner=FakeRunner(*(c1_reply(k, offset=10) for k in range(1, 11))))

    doc = story_store.read_doc(story_id, "concepts.json")
    assert schemas.story_concepts_errors(doc) == []
    assert [card["concept_id"] for card in doc["concepts"]] == [f"gen_{n:02d}" for n in range(1, 21)]
    assert summary["concept_ids"] == [f"gen_{n:02d}" for n in range(11, 21)]
    # 10 library + 11 titles on call 2: over the pack's 20, said once, not per call.
    trimmed = [line for line in log if line.startswith("✂️")]
    assert trimmed == ["✂️ The list of titles to avoid was trimmed for the prompt (the context pack is budgeted)."]


def test_numbering_goes_to_three_digits_past_99(story_store):
    m = _new()
    story_id = _story(story_store)
    card = dict(_c1_concept("Ancien"), concept_id="gen_99", source="generated",
                prompt_version="s1", created_at=NOW, language="fr")
    story_store.write_doc(story_id, "concepts.json",
                          {"$schema": "story_concepts_v1", "concepts": [card], "updated_at": NOW},
                          now=NOW)
    ctx, _ = _ctx(story_store, story_id)

    summary = m.concepts.run(ctx, runner=FakeRunner(*(c1_reply(k) for k in range(1, 11))))

    assert summary["concept_ids"] == [f"gen_{n}" for n in range(100, 110)]
    assert schemas.story_concepts_errors(story_store.read_doc(story_id, "concepts.json")) == []


def test_each_call_avoids_the_library_titles_and_every_earlier_title(story_store):
    m = _new()
    story_id = _story(story_store)
    ctx, _ = _ctx(story_store, story_id)
    runner = FakeRunner(*(c1_reply(k) for k in range(1, 11)))

    m.concepts.run(ctx, runner=runner)

    assert len(runner.calls) == 10
    for k, call in enumerate(runner.calls, 1):
        avoid = _avoid_titles(call["user"])
        earlier = [f"Titre {n}" for n in range(1, k)]
        assert set(avoid) == set(LIBRARY_TITLES_FR) | set(earlier), f"call {k}"
        # The library first, so it is never what the pack's cap cuts.
        assert avoid[: len(LIBRARY_TITLES_FR)] == LIBRARY_TITLES_FR


def test_a_story_without_a_style_sends_no_style_line_and_its_seed(story_store):
    m = _new()
    story_id = _story(story_store, style=None, seed="Des fruits complotent sur une île.")
    ctx, _ = _ctx(story_store, story_id)
    runner = FakeRunner(*(c1_reply(k) for k in range(1, 11)))

    m.concepts.run(ctx, runner=runner)

    for call in runner.calls:
        assert "Visual style" not in call["user"]
        assert "Seed idea from the user: Des fruits complotent sur une île." in call["user"]


def test_a_call_rejected_twice_is_reported_and_the_others_are_kept(story_store):
    m = _new()
    story_id = _story(story_store)
    ctx, log = _ctx(story_store, story_id)
    runner = FakeRunner(c1_reply(1), INVALID_C1, INVALID_C1, *(c1_reply(k) for k in range(3, 11)))

    summary = m.concepts.run(ctx, runner=runner)

    assert len(runner.calls) == 11
    assert runner.calls[1]["max_tokens"] == runner.calls[2]["max_tokens"] == 700
    doc = story_store.read_doc(story_id, "concepts.json")
    assert [card["concept_id"] for card in doc["concepts"]] == [f"gen_{n:02d}" for n in range(1, 10)]
    assert [card["title"] for card in doc["concepts"]] == [f"Titre {n}" for n in (1, 3, 4, 5, 6, 7, 8, 9, 10)]
    assert summary["failed_calls"] == [2]
    assert summary["generated"] == 9
    assert "⚠️ C1 reply rejected ($.concepts: 2 concept(s), expected exactly 1); asking once more with the same cap" in log
    assert any(line.startswith("✖ C1 call 2/10 failed: the reply failed validation twice: $.concepts: 2 concept(s)")
               for line in log)
    assert log[-1] == "Generated 9 of 10 concepts (calls failed: 2)"


def test_every_call_failing_is_a_step_failure_naming_each_reason(story_store):
    m = _new()
    story_id = _story(story_store)
    ctx, log = _ctx(story_store, story_id)
    runner = FakeRunner(*(
        ProviderError("Every provider in the chain failed (1 tried)",
                      failures=[("gemini/gemini-test", f"RuntimeError: outage {k}")])
        for k in range(1, 11)
    ))

    with pytest.raises(steps.StepFailed) as caught:
        m.concepts.run(ctx, runner=runner)

    message = str(caught.value)
    assert message.startswith("No concept was generated: every C1 call failed (")
    for k in range(1, 11):
        assert (f"call {k}/10: every provider in the chain failed (1 tried): "
                f"gemini/gemini-test: RuntimeError: outage {k}") in message
    assert len(runner.calls) == 10  # a failed chain is not asked again
    assert sum(line.startswith("✖ C1 call") for line in log) == 10
    assert story_store.read_doc(story_id, "concepts.json") is None


def test_a_cancel_during_call_2_keeps_two_cards_and_makes_no_third_call(story_store):
    m = _new()
    story_id = _story(story_store)
    token = CancelToken()
    ctx, _ = _ctx(story_store, story_id, token=token)

    def cancel_then_answer(call):
        token.cancel()  # the reply was already on its way: it is kept
        return c1_reply(2)

    runner = FakeRunner(c1_reply(1), cancel_then_answer, c1_reply(3))

    with pytest.raises(Cancelled):
        m.concepts.run(ctx, runner=runner)

    assert len(runner.calls) == 2
    doc = story_store.read_doc(story_id, "concepts.json")
    assert [card["concept_id"] for card in doc["concepts"]] == ["gen_01", "gen_02"]


def test_concepts_json_is_written_after_each_call(story_store):
    m = _new()
    story_id = _story(story_store)
    ctx, _ = _ctx(story_store, story_id)
    seen = []

    def look(reply):
        def answer(call):
            seen.append(story_store.read_doc(story_id, "concepts.json"))
            return reply
        return answer

    def look_then_crash(call):
        seen.append(story_store.read_doc(story_id, "concepts.json"))
        raise RuntimeError("the worker died")

    runner = FakeRunner(look(c1_reply(1)), look(c1_reply(2)), look_then_crash)

    with pytest.raises(RuntimeError, match="the worker died"):
        m.concepts.run(ctx, runner=runner)

    assert seen[0] is None
    assert len(seen[1]["concepts"]) == 1
    assert len(seen[2]["concepts"]) == 2
    doc = story_store.read_doc(story_id, "concepts.json")
    assert schemas.story_concepts_errors(doc) == []
    assert len(doc["concepts"]) == 2


def test_concepts_for_a_story_that_does_not_exist(story_store):
    m = _new()
    ctx, _ = _ctx(story_store, "0123456789ab")
    runner = FakeRunner()

    with pytest.raises(steps.StepFailed, match="There is no story '0123456789ab'"):
        m.concepts.run(ctx, runner=runner)
    assert runner.calls == []


def test_an_invalid_concepts_file_is_refused_before_anything_is_spent(story_store, tmp_path):
    m = _new()
    story_id = _story(story_store)
    path = Path(story_store.story_dir(story_id)) / "concepts.json"
    path.write_text(json.dumps({"$schema": "story_concepts_v1", "concepts": [{"concept_id": "x"}],
                                "updated_at": NOW}), encoding="utf-8")
    before = path.read_bytes()
    ctx, _ = _ctx(story_store, story_id)
    runner = FakeRunner()

    with pytest.raises(steps.StepFailed, match="concepts.json is not a valid story_concepts_v1 document"):
        m.concepts.run(ctx, runner=runner)
    assert runner.calls == []
    assert path.read_bytes() == before


def test_story_concepts_schema_refuses_a_foreign_key_and_a_duplicate_id():
    card = dict(_c1_concept("Un"), concept_id="gen_01", source="generated",
                prompt_version="s1", created_at=NOW, language="fr")
    doc = {"$schema": "story_concepts_v1", "concepts": [card], "updated_at": NOW}
    assert schemas.story_concepts_errors(doc) == []

    assert any("additional property" in e for e in
               schemas.story_concepts_errors(dict(doc, concepts=[dict(card, extra=1)])))
    assert any("used twice" in e for e in
               schemas.story_concepts_errors(dict(doc, concepts=[card, dict(card)])))
    assert schemas.story_concepts_errors(dict(doc, concepts=[dict(card, concept_id="gen_00")]))
    assert schemas.story_concepts_errors(dict(doc, concepts=[dict(card, concept_id="gen_1")]))


# =================================================================== bible

def test_bible_runs_b1_b2_b3_in_order_and_writes_after_each(story_store):
    m = _new()
    story_id = _story(story_store)
    ctx, log = _ctx(story_store, story_id, step="bible")
    before_each = []

    def look(reply):
        def answer(call):
            before_each.append(story_store.get(story_id))
            return reply
        return answer

    runner = FakeRunner(look(B1_REPLY), look(B2_REPLY), look(B3_REPLY))

    summary = m.bible.run(ctx, runner=runner)

    assert [call["schema_name"] for call in runner.calls] == ["bible_core", "bible_world", "bible_values"]
    assert [call["max_tokens"] for call in runner.calls] == [400, 520, 300]
    assert [call["temperature"] for call in runner.calls] == [0.5, 0.5, 0.5]

    assert before_each[0]["logline"] is None
    assert before_each[1]["logline"] == B1_REPLY["logline"] and before_each[1]["world"] is None
    assert before_each[2]["world"] == B2_REPLY and before_each[2]["themes_and_values"] == []
    # Each prompt is built from the story as it then stands.
    assert B1_REPLY["logline"] not in runner.calls[0]["user"]
    assert "Bible written so far:\n" + B1_REPLY["logline"] in runner.calls[1]["user"]
    assert "Setting: " + B2_REPLY["setting_summary"] in runner.calls[2]["user"]

    story = story_store.get(story_id)
    for key in B1_REPLY:
        assert story[key] == B1_REPLY[key]
    assert story["world"] == B2_REPLY
    for key in B3_REPLY:
        assert story[key] == B3_REPLY[key]
    assert schemas.story_bible_errors(story) == []

    assert summary == {"written": ["B1", "B2", "B3"]}
    written = [line for line in log if line.startswith("✍️")]
    assert [line.split(" ≈")[0] for line in written] == [
        "✍️ B1 via gemini/gemini-test", "✍️ B2 via gemini/gemini-test", "✍️ B3 via gemini/gemini-test"]
    assert [line.rsplit(" (", 1)[1] for line in written] == ["cap 400)", "cap 520)", "cap 300)"]


def test_rewriting_an_approved_bible_clears_its_approval_and_leaves_style(story_store):
    m = _new()
    story_id = _story(story_store)
    story_store.update(story_id, lambda doc: doc["approvals"].update(bible=NOW, style=NOW), now=NOW)
    assert story_store.get(story_id)["status"] == "style_approved"
    ctx, _ = _ctx(story_store, story_id, step="bible")

    m.bible.run(ctx, runner=FakeRunner(B1_REPLY, B2_REPLY, B3_REPLY))

    story = story_store.get(story_id)
    assert story["approvals"] == {"concept": NOW, "bible": None, "style": NOW}
    assert story["status"] == "concept_chosen"


def test_a_failed_b2_keeps_b1_and_b3_and_names_world(story_store):
    m = _new()
    story_id = _story(story_store)
    ctx, log = _ctx(story_store, story_id, step="bible")
    runner = FakeRunner(B1_REPLY, INVALID_B2, INVALID_B2, B3_REPLY)

    with pytest.raises(steps.StepFailed) as caught:
        m.bible.run(ctx, runner=runner)

    message = str(caught.value)
    assert message.startswith("Bible incomplete: B2 failed (the reply failed validation twice: $.rules: 1 rule(s)")
    assert message.endswith(". Regenerate 'world' to finish it.")
    assert [call["schema_name"] for call in runner.calls] == [
        "bible_core", "bible_world", "bible_world", "bible_values"]
    story = story_store.get(story_id)
    assert story["logline"] == B1_REPLY["logline"]
    assert story["world"] is None
    assert story["themes_and_values"] == B3_REPLY["themes_and_values"]
    assert any(line.startswith("✖ B2 failed: the reply failed validation twice") for line in log)


def test_a_failed_b1_names_every_target_that_finishes_it(story_store):
    m = _new()
    story_id = _story(story_store)
    ctx, _ = _ctx(story_store, story_id, step="bible")
    outage = ProviderError("x", failures=[("gemini/gemini-test", "APITimeoutError: timed out")])
    runner = FakeRunner(outage, B2_REPLY, B3_REPLY)

    with pytest.raises(steps.StepFailed) as caught:
        m.bible.run(ctx, runner=runner)

    assert str(caught.value) == (
        "Bible incomplete: B1 failed (every provider in the chain failed (1 tried): "
        "gemini/gemini-test: APITimeoutError: timed out). "
        "Regenerate 'logline', 'premise' and 'tone' to finish it."
    )
    story = story_store.get(story_id)
    assert story["logline"] is None and story["world"] == B2_REPLY


def test_a_reply_the_story_would_refuse_is_asked_for_again(story_store):
    """A 45-character tag passes B1's word count but not story_bible_v1."""
    m = _new()
    story_id = _story(story_store)
    ctx, log = _ctx(story_store, story_id, step="bible")
    too_long = dict(B1_REPLY, genre_tags=["a" * 45, "soap"])
    runner = FakeRunner(too_long, B1_REPLY, B2_REPLY, B3_REPLY)

    m.bible.run(ctx, runner=runner)

    assert story_store.get(story_id)["genre_tags"] == B1_REPLY["genre_tags"]
    assert any(line.startswith("⚠️ B1 reply rejected ($.genre_tags[0]: length 45 > maxLength 40)")
               for line in log)


def test_bible_without_a_chosen_concept(story_store):
    m = _new()
    story_id = _story(story_store, chosen=False)
    ctx, _ = _ctx(story_store, story_id, step="bible")
    runner = FakeRunner()

    with pytest.raises(steps.StepFailed) as caught:
        m.bible.run(ctx, runner=runner)
    assert str(caught.value) == "Choose a concept first."
    assert runner.calls == []


def test_the_real_chain_never_contacts_a_keyless_link_and_its_json_reaches_the_story(story_store):
    """RC-C1's mirror: groq is in the chain, has no key, and is never built."""
    m = _new()
    story_id = _story(story_store)
    settings = {"LLM_CHAIN": "groq/groq-test,gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key"}
    ctx, log = _ctx(story_store, story_id, step="bible", settings_env=settings)
    factory = responder([json.dumps(B1_REPLY), json.dumps(B2_REPLY), json.dumps(B3_REPLY)])
    runner = functools.partial(llm.run_chain, client_factory=factory, sleep_fn=_no_sleep)

    m.bible.run(ctx, runner=runner)

    assert factory.constructed == ["gemini", "gemini", "gemini"]
    assert [provider for provider, _ in factory.seen] == ["gemini", "gemini", "gemini"]
    body = factory.seen[0][1]
    assert body["model"] == "gemini-test"
    assert body["max_tokens"] == 400 and body["temperature"] == 0.5
    assert body["response_format"]["json_schema"]["name"] == "bible_core"

    skip = "   ⏭ Skipping groq/groq-test: no API key (GROQ_API_KEY is not set)."
    attempt = "   🔁 gemini/gemini-test attempt 1/3..."
    assert log.count(skip) == 3 and log.count(attempt) == 3
    first_written = next(i for i, line in enumerate(log) if line.startswith("✍️ B1 via gemini/gemini-test"))
    assert log.index(skip) < log.index(attempt) < first_written

    story = story_store.get(story_id)
    assert story["logline"] == B1_REPLY["logline"]
    assert story["world"] == B2_REPLY
    assert story["audience"] == B3_REPLY["audience"]


# ============================================================== regenerate

def test_regenerate_tone_with_a_note_changes_only_tone_and_genre_tags(story_store):
    m = _new()
    story_id = _bible_written(story_store, m)
    before = story_store.get(story_id)
    rewritten = {
        "logline": "Une toute autre accroche que le modèle a renvoyée quand même.",
        "premise": "Une autre prémisse. Elle ne doit pas être appliquée.",
        "tone": "sombre, tendu, sans pitié",
        "genre_tags": ["drame", "survie"],
    }
    ctx, log = _ctx(story_store, story_id, step="regenerate",
                    params={"target": "bible:tone", "note": "plus sombre"})
    runner = FakeRunner(rewritten)

    result = m.regenerate.run(ctx, runner=runner)

    after = story_store.get(story_id)
    assert {key for key in before if before[key] != after[key]} == {"tone", "genre_tags", "updated_at"}
    assert after["tone"] == rewritten["tone"]
    assert after["genre_tags"] == rewritten["genre_tags"]

    call, = runner.calls
    assert call["schema_name"] == "bible_core" and call["max_tokens"] == 400
    assert "Rewrite only `tone`, following the author's note: plus sombre" in call["user"]
    assert f"- tone: {B1_REPLY['tone']}" in call["user"]
    assert result == {"target": "bible:tone", "fields": ["tone", "genre_tags"]}
    assert log[-1] == "🔁 Regenerated tone (note: plus sombre)"


def test_regenerate_world_replaces_all_four_keys_and_clears_the_approval(story_store):
    m = _new()
    story_id = _bible_written(story_store, m)
    story_store.update(story_id, lambda doc: doc["approvals"].update(bible=NOW), now=NOW)
    before = story_store.get(story_id)
    assert before["status"] == "bible_approved"
    new_world = {
        "setting_summary": "Un archipel de plateaux flottants où chaque île est un studio.",
        "rules": ["Règle un.", "Règle deux.", "Règle trois.", "Règle quatre.", "Règle cinq."],
        "time_period": "futur proche",
        "recurring_motifs": ["la cloche", "le radeau", "la caméra cassée"],
    }
    ctx, log = _ctx(story_store, story_id, step="regenerate", params={"target": "bible:world"})
    runner = FakeRunner(new_world)

    m.regenerate.run(ctx, runner=runner)

    after = story_store.get(story_id)
    assert after["world"] == new_world
    assert after["approvals"]["bible"] is None
    assert after["status"] == "concept_chosen"
    assert {key for key in before if before[key] != after[key]} == {"world", "approvals", "status", "updated_at"}
    user = runner.calls[0]["user"]
    assert "Rewrite only `world`, and keep every other field exactly as it is." in user
    assert f"- setting_summary: {B2_REPLY['setting_summary']}" in user
    assert log[-1] == "🔁 Regenerated world"


def test_regenerate_concepts_appends_ten_with_the_note_and_needs_no_concept(story_store):
    m = _new()
    story_id = _story(story_store, chosen=False, seed="Des fruits complotent sur une île.")
    m.concepts.run(_ctx(story_store, story_id)[0], runner=FakeRunner(*(c1_reply(k) for k in range(1, 11))))
    ctx, log = _ctx(story_store, story_id, step="regenerate",
                    params={"target": "concepts", "note": "plus sombre"})
    runner = FakeRunner(*(c1_reply(k, offset=10) for k in range(1, 11)))

    result = m.regenerate.run(ctx, runner=runner)

    doc = story_store.read_doc(story_id, "concepts.json")
    assert len(doc["concepts"]) == 20
    assert result["concept_ids"] == [f"gen_{n:02d}" for n in range(11, 21)]
    for call in runner.calls:
        assert ("Seed idea from the user: Author's note: plus sombre\n"
                "Des fruits complotent sur une île.") in call["user"]
    assert log[-1] == "Generated 10 of 10 concepts."


@pytest.mark.parametrize("target", ["bible:nope", "bible:", "bible", "character:c1:text", "concepts:", None, 7])
def test_an_unknown_target_names_the_valid_ones(story_store, target):
    m = _new()
    story_id = _story(story_store)
    ctx, _ = _ctx(story_store, story_id, step="regenerate", params={"target": target})
    runner = FakeRunner()

    with pytest.raises(steps.StepFailed) as caught:
        m.regenerate.run(ctx, runner=runner)

    message = str(caught.value)
    assert message.startswith(f"Cannot regenerate {target!r}: the valid targets are ")
    for valid in ("bible:logline", "bible:premise", "bible:tone", "bible:world", "bible:themes", "concepts"):
        assert valid in message
    assert runner.calls == []


def test_regenerating_a_bible_field_needs_a_chosen_concept(story_store):
    m = _new()
    story_id = _story(story_store, chosen=False)
    ctx, _ = _ctx(story_store, story_id, step="regenerate", params={"target": "bible:themes", "note": "x"})
    runner = FakeRunner()

    with pytest.raises(steps.StepFailed) as caught:
        m.regenerate.run(ctx, runner=runner)
    assert str(caught.value) == "Choose a concept first."
    assert runner.calls == []


# =============================================================== call_json

def _b1_prompt():
    pack = context.build_pack(language="fr", concept=templates.localize_concept(TENTAFRUIT, "fr"))
    return prompts.build_b1(pack)


def _bare_ctx(tmp_path, token=None, settings_env=None):
    log = Log()
    ctx = steps.StepContext(
        job_id="job000000001", story_id="0123456789ab", step="bible", ep=None, params={},
        cancel=token or CancelToken(),
        settings_env=dict(SETTINGS) if settings_env is None else settings_env,
        outputs_dir=str(tmp_path), on_log=log,
    )
    return ctx, log


def test_a_reply_rejected_once_is_asked_for_again_with_the_same_cap(tmp_path):
    m = _new()
    ctx, log = _bare_ctx(tmp_path)
    runner = FakeRunner(INVALID_B1, B1_REPLY)

    value = m.llm_call.call_json(ctx, "B1", *_b1_prompt(), validator=schemas.b1_errors, runner=runner)

    assert value == B1_REPLY
    assert [call["max_tokens"] for call in runner.calls] == [400, 400]
    assert [call["temperature"] for call in runner.calls] == [0.5, 0.5]
    errors = schemas.b1_errors(INVALID_B1)
    assert len(errors) == 3
    warned = [line for line in log if line.startswith("⚠️")]
    assert warned == [f"⚠️ B1 reply rejected ({errors[0]}; {errors[1]}); asking once more with the same cap"]
    assert log[-1].startswith("✍️ B1 via gemini/gemini-test")


def test_the_accepted_line_names_the_link_the_estimate_and_the_cap(tmp_path):
    m = _new()
    ctx, log = _bare_ctx(tmp_path)
    runner = FakeRunner(B1_REPLY, link=Link("gemini", "gemini-flash-lite-latest"))

    m.llm_call.call_json(ctx, "B1", *_b1_prompt(), validator=schemas.b1_errors, runner=runner)

    tokens = pacing.estimate_tokens(json.dumps(B1_REPLY, ensure_ascii=False))
    assert log == [f"✍️ B1 via gemini/gemini-flash-lite-latest ≈{tokens} tokens out (cap 400)"]


def test_a_reply_rejected_twice_is_a_step_failure(tmp_path):
    m = _new()
    ctx, log = _bare_ctx(tmp_path)
    runner = FakeRunner(INVALID_B1, INVALID_B1)

    with pytest.raises(steps.StepFailed) as caught:
        m.llm_call.call_json(ctx, "B1", *_b1_prompt(), validator=schemas.b1_errors, runner=runner)

    errors = schemas.b1_errors(INVALID_B1)
    assert str(caught.value) == f"B1: the reply failed validation twice: {'; '.join(errors)}"
    assert caught.value.reason == f"the reply failed validation twice: {'; '.join(errors)}"
    assert [call["max_tokens"] for call in runner.calls] == [400, 400]
    assert not any(line.startswith("✍️") for line in log)


def test_a_chain_that_failed_is_a_step_failure_carrying_every_links_reason(tmp_path):
    m = _new()
    ctx, _ = _bare_ctx(tmp_path)
    exc = ProviderError(
        "Every provider in the chain failed (2 tried):\n  groq/a: ...\n  gemini/b: ...",
        failures=[("groq/a", "no API key (GROQ_API_KEY is not set)"),
                  ("gemini/b", "InternalServerError: 503\nupstream")],
    )
    runner = FakeRunner(exc)

    with pytest.raises(steps.StepFailed) as caught:
        m.llm_call.call_json(ctx, "B2", *_b1_prompt(), validator=schemas.b1_errors, runner=runner)

    assert str(caught.value) == (
        "B2: every provider in the chain failed (2 tried): "
        "groq/a: no API key (GROQ_API_KEY is not set); gemini/b: InternalServerError: 503 upstream"
    )
    assert caught.value.__cause__ is exc
    assert len(runner.calls) == 1


def test_each_run_gets_its_own_budgeted_deadline_the_keys_and_the_token(tmp_path):
    m = _new()
    token = CancelToken()
    ctx, _ = _bare_ctx(tmp_path, token=token)
    clock = [1000.0]

    def time_fn():
        return clock[0]

    def slow(reply):
        def answer(call):
            clock[0] += 40.0
            return reply
        return answer

    runner = FakeRunner(slow(INVALID_B1), B1_REPLY)

    m.llm_call.call_json(ctx, "B1", *_b1_prompt(), validator=schemas.b1_errors, runner=runner, time_fn=time_fn)

    assert m.llm_call.STORY_CALL_BUDGET_SECONDS == 300
    assert [call["deadline"] for call in runner.calls] == [1300.0, 1340.0]
    assert all(call["time_fn"] is time_fn for call in runner.calls)
    assert all(call["cancel"] is token for call in runner.calls)
    assert all(call["keys"] == {"gemini": "test-gemini-key"} for call in runner.calls)
    assert all(call["schema"] is _b1_prompt()[2] for call in runner.calls)


def test_a_prompt_over_the_pack_budget_is_never_sent(tmp_path):
    m = _new()
    ctx, _ = _bare_ctx(tmp_path)
    runner = FakeRunner(B1_REPLY)

    with pytest.raises(ValueError, match="over the 1200-token budget"):
        m.llm_call.call_json(ctx, "B1", "system", "word " * 6000, {}, validator=schemas.b1_errors, runner=runner)
    assert runner.calls == []


def test_a_cancelled_job_sends_nothing(tmp_path):
    m = _new()
    token = CancelToken()
    token.cancel()
    ctx, _ = _bare_ctx(tmp_path, token=token)
    runner = FakeRunner(B1_REPLY)

    with pytest.raises(Cancelled):
        m.llm_call.call_json(ctx, "B1", *_b1_prompt(), validator=schemas.b1_errors, runner=runner)
    assert runner.calls == []


# ========================================================= chain and keys

def test_the_settings_chain_beats_the_process_env(monkeypatch):
    m = _new()
    monkeypatch.setenv("LLM_CHAIN", "groq/from-process")

    assert m.llm_call.resolve_chain({"LLM_CHAIN": "gemini/from-settings"}) == [Link("gemini", "from-settings")]
    assert m.llm_call.resolve_chain({}) == [Link("groq", "from-process")]
    # A blank Settings value falls through, as it does for a clip job.
    assert m.llm_call.resolve_chain({"LLM_CHAIN": "  "}) == [Link("groq", "from-process")]


@pytest.mark.parametrize("settings_env", [{"LLM_CHAIN": ""}, {"LLM_CHAIN": "   "}, {}, None])
def test_an_empty_chain_spec_falls_back_to_the_default_chain(settings_env):
    m = _new()
    assert m.llm_call.resolve_chain(settings_env) == registry.parse_chain(registry.DEFAULT_LLM_CHAIN)


def test_the_settings_keys_beat_the_process_env(monkeypatch):
    m = _new()
    monkeypatch.setenv("GOOGLE_API_KEY", "process-gemini")
    monkeypatch.setenv("GROQ_API_KEY", "process-groq")

    keys = m.llm_call.resolve_keys({"GOOGLE_API_KEY": "settings-gemini"})

    assert keys == {"gemini": "settings-gemini", "groq": "process-groq"}


def test_a_provider_with_no_key_is_left_out(monkeypatch):
    m = _new()
    monkeypatch.setenv("GOOGLE_API_KEY", "process-gemini")

    assert m.llm_call.resolve_keys({}) == {"gemini": "process-gemini"}
    # web/api/config_adapter's precedence: a Settings value, even an empty
    # one, is the Settings answer.
    assert m.llm_call.resolve_keys({"GOOGLE_API_KEY": "", "GROQ_API_KEY": "settings-groq"}) == {
        "groq": "settings-groq"}
    assert m.llm_call.resolve_keys(None) == {"gemini": "process-gemini"}


# ============================================ paid links need allow_paid (DEC-097)

PAID_REASON = "paid link: allow_paid is off (AI Story spends only on opt-in)"
PAID_SKIP_LINE = ("   ⏭ Skipping openrouter/test-model: paid link, allow_paid is off "
                  "(AI Story spends only on opt-in).")
# Test values only: every client below is fake.
PAID_SETTINGS = {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model",
                 "GOOGLE_API_KEY": "test-gemini-key", "OPENROUTER_API_KEY": "test-openrouter-key"}


@pytest.mark.parametrize("spec,allow_paid,usable", [
    ("groq/openai/gpt-oss-120b", "", True),
    ("gemini/gemini-test", "", True),
    ("mistral/mistral-small-latest", "", True),
    ("nvidia/nvidia/nemotron-test", "", True),
    ("custom/local-model", "", True),
    ("openrouter/mistralai/mistral-small-3.2-24b-instruct", "", False),
    ("openrouter/qwen/qwen3.8-27b:free", "", True),
    ("openrouter/qwen/qwen3.8-27b:free-ish", "", False),
    ("openrouter/mistralai/mistral-small-3.2-24b-instruct", "1", True),
    ("openrouter/qwen/qwen3.8-27b:free", "1", True),
    ("gemini/gemini-test", "1", True),
])
def test_story_chain_skips_a_paid_link_only_while_allow_paid_is_off(spec, allow_paid, usable):
    m = _new()
    link = registry.parse_spec(spec)

    result = m.llm_call.story_chain({"LLM_CHAIN": spec, "ALLOW_PAID": allow_paid})

    assert result == (([link], []) if usable else ([], [(link, PAID_REASON)]))
    assert m.llm_call.PAID_SKIP_REASON == PAID_REASON


def test_story_chain_keeps_the_order_and_reads_allow_paid_like_the_preview_does(monkeypatch):
    m = _new()
    default = registry.parse_chain(registry.DEFAULT_LLM_CHAIN)
    paid = [link for link in default if link.provider == "openrouter"]

    assert m.llm_call.story_chain({}) == ([link for link in default if link not in paid],
                                          [(link, PAID_REASON) for link in paid])
    # The process env counts when Settings do not name it; a Settings value
    # replaces it, an empty one turns it off (gating.merged_env).
    monkeypatch.setenv("ALLOW_PAID", "1")
    assert m.llm_call.story_chain({}) == (default, [])
    assert m.llm_call.story_chain({"ALLOW_PAID": ""})[1] == [(link, PAID_REASON) for link in paid]
    with pytest.raises(ValueError, match="DAILY_CAP_USD"):
        m.llm_call.story_chain({"DAILY_CAP_USD": "lots"})


def _gemini_down_openrouter_up(contents):
    """A ``client_factory`` for the real ``run_chain``: every gemini request
    raises, openrouter answers *contents* in order. Records each client built."""
    queue = list(contents)
    constructed = []
    seen = []

    class Completions:
        def __init__(self, provider):
            self.provider = provider

        def create(self, **kwargs):
            seen.append(self.provider)
            if self.provider == "gemini":
                raise RuntimeError("gemini outage")
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=queue.pop(0)))],
                usage=SimpleNamespace(total_tokens=120),
            )

    def factory(link, **kwargs):
        constructed.append(link.provider)
        return SimpleNamespace(chat=SimpleNamespace(completions=Completions(link.provider)))

    factory.constructed = constructed
    factory.seen = seen
    return factory


def test_with_allow_paid_off_the_paid_link_is_never_built_and_the_failure_names_it(story_store):
    """The live failure of 2026-09-26, replayed: gemini fails every call and
    the chain would fall through to the paid OpenRouter link."""
    m = _new()
    story_id = _story(story_store)
    ctx, log = _ctx(story_store, story_id, settings_env=dict(PAID_SETTINGS, ALLOW_PAID=""))
    factory = _gemini_down_openrouter_up([json.dumps(c1_reply(k), ensure_ascii=False) for k in range(1, 11)])
    runner = functools.partial(llm.run_chain, client_factory=factory, sleep_fn=_no_sleep)

    with pytest.raises(steps.StepFailed) as caught:
        m.concepts.run(ctx, runner=runner)

    assert factory.constructed == ["gemini"] * 10
    assert factory.seen == ["gemini"] * 10
    assert log.count(PAID_SKIP_LINE) == 10
    assert log.index(PAID_SKIP_LINE) < log.index("   🔁 gemini/gemini-test attempt 1/3...")
    message = str(caught.value)
    assert message.startswith("No concept was generated: every C1 call failed (call 1/10: ")
    assert message.count(f"openrouter/test-model not tried: {PAID_REASON}") == 10
    assert story_store.read_doc(story_id, "concepts.json") is None


def test_with_allow_paid_on_the_paid_link_is_called(story_store):
    m = _new()
    story_id = _story(story_store)
    ctx, log = _ctx(story_store, story_id, settings_env=dict(PAID_SETTINGS, ALLOW_PAID="1"))
    factory = _gemini_down_openrouter_up([json.dumps(c1_reply(k), ensure_ascii=False) for k in range(1, 11)])
    runner = functools.partial(llm.run_chain, client_factory=factory, sleep_fn=_no_sleep)

    summary = m.concepts.run(ctx, runner=runner)

    assert summary["generated"] == 10
    assert factory.constructed.count("openrouter") == 10
    assert sum(line.startswith("✍️ C1 via openrouter/test-model ≈") for line in log) == 10
    assert not any("allow_paid is off" in line for line in log)


def test_the_chain_handed_to_run_chain_leaves_the_paid_link_out(tmp_path):
    m = _new()
    ctx, log = _bare_ctx(tmp_path, settings_env=dict(PAID_SETTINGS))
    runner = FakeRunner(INVALID_B1, B1_REPLY)

    m.llm_call.call_json(ctx, "B1", *_b1_prompt(), validator=schemas.b1_errors, runner=runner)

    assert [call["chain"] for call in runner.calls] == [[LINK], [LINK]]
    assert runner.calls[0]["keys"] == {"gemini": "test-gemini-key", "openrouter": "test-openrouter-key"}
    # Printed before each chain run, as the chain prints its own hops.
    assert log.count(PAID_SKIP_LINE) == 2 and log[0] == PAID_SKIP_LINE


def test_a_chain_whose_only_keyed_link_is_paid_sends_nothing_and_says_what_to_set(tmp_path):
    m = _new()
    settings = {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model", "OPENROUTER_API_KEY": "test-openrouter-key"}
    ctx, log = _bare_ctx(tmp_path, settings_env=settings)
    runner = FakeRunner(B1_REPLY)

    with pytest.raises(steps.StepFailed) as caught:
        m.llm_call.call_json(ctx, "B1", *_b1_prompt(), validator=schemas.b1_errors, runner=runner)

    assert runner.calls == []
    assert log == [PAID_SKIP_LINE]
    assert str(caught.value) == (
        "B1: The only keyed link of the LLM chain is paid (openrouter/test-model), and allow_paid is "
        "off: AI Story spends only on opt-in. Set the key of a free link, one of: "
        "GOOGLE_API_KEY (https://aistudio.google.com/apikey); or turn allow_paid on to use it."
    )


def test_a_chain_with_no_free_link_names_one_to_add(tmp_path):
    m = _new()
    settings = {"LLM_CHAIN": "openrouter/test-model", "OPENROUTER_API_KEY": "test-openrouter-key"}
    ctx, _ = _bare_ctx(tmp_path, settings_env=settings)
    runner = FakeRunner(B1_REPLY)

    with pytest.raises(steps.StepFailed) as caught:
        m.llm_call.call_json(ctx, "B1", *_b1_prompt(), validator=schemas.b1_errors, runner=runner)

    assert runner.calls == []
    assert caught.value.reason.endswith(
        "LLM_CHAIN has no free link: add one, e.g. gemini/gemini-3.5-flash-lite with GOOGLE_API_KEY; "
        "or turn allow_paid on to use it.")


def test_budget_settings_that_cannot_be_read_send_nothing(tmp_path):
    m = _new()
    ctx, _ = _bare_ctx(tmp_path, settings_env=dict(SETTINGS, PER_STORY_CAP_USD="ten"))
    runner = FakeRunner(B1_REPLY)

    with pytest.raises(steps.StepFailed, match="B1: The budget settings cannot be used: PER_STORY_CAP_USD"):
        m.llm_call.call_json(ctx, "B1", *_b1_prompt(), validator=schemas.b1_errors, runner=runner)
    assert runner.calls == []


# ================================================================ registry

def test_the_phase_1_steps_are_registered():
    for name in ("concepts", "bible", "regenerate"):
        assert name in steps.RUNNERS, name
        assert callable(steps.RUNNERS[name])


def test_importing_the_registry_imports_no_prompt_or_llm_module():
    code = (
        "import sys, clipping.aistory.steps\n"
        "heavy = ('clipping.providers.llm', 'clipping.aistory.prompts', 'clipping.config',\n"
        "         'clipping.aistory.steps.llm_call', 'clipping.aistory.steps.bible')\n"
        "print(sorted(name for name in heavy if name in sys.modules))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=REPO_ROOT, capture_output=True,
                          text=True, check=True)
    assert done.stdout.strip() == "[]"


def test_the_registry_runs_the_bible_step(story_store):
    story_id = _story(story_store, chosen=False)
    ctx, _ = _ctx(story_store, story_id, step="bible")

    with pytest.raises(Exception) as caught:
        steps.run("bible", ctx)

    assert type(caught.value).__name__ == "StepFailed", repr(caught.value)
    assert str(caught.value) == "Choose a concept first."


def test_the_registry_runs_regenerate(story_store):
    story_id = _story(story_store)
    ctx, _ = _ctx(story_store, story_id, step="regenerate", params={"target": "season:1"})

    with pytest.raises(Exception) as caught:
        steps.run("regenerate", ctx)

    assert type(caught.value).__name__ == "StepFailed", repr(caught.value)
    assert str(caught.value).startswith("Cannot regenerate 'season:1'")


def test_the_registry_runs_concepts_through_the_real_chain_by_default(story_store, monkeypatch):
    """No runner handed in: the step uses ``llm.run_chain``, looked up when the
    call is made -- here the real one, with a fake client behind it."""
    story_id = _story(story_store)
    ctx, log = _ctx(story_store, story_id)
    factory = responder([json.dumps(c1_reply(k), ensure_ascii=False) for k in range(1, 11)])
    monkeypatch.setattr(llm, "run_chain", functools.partial(llm.run_chain, client_factory=factory,
                                                            sleep_fn=_no_sleep))

    summary = steps.run("concepts", ctx)

    assert summary["generated"] == 10
    assert factory.constructed == ["gemini"] * 10
    assert [body["max_tokens"] for _, body in factory.seen] == [700] * 10
    assert log.count("   🔁 gemini/gemini-test attempt 1/3...") == 10
    doc = story_store.read_doc(story_id, "concepts.json")
    assert [card["title"] for card in doc["concepts"]] == [f"Titre {n}" for n in range(1, 11)]
