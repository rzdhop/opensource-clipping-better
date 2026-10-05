"""A twist that brings an appearance variant (plan 23 stage D5): N1v2.

A story whose characters may carry variants (``media_policy.variants_enabled``:
a v2 story with a ``sheet_mode``, or ``variants: "on"``) asks N1v2 instead of
N1 -- N1's prompt, then the characters a twist may give a variant and the ask;
each twist's ``variant`` is null or ``{char_id, label, delta_text}``. Accepting
such a twist (``workflow.decide_proposal``) creates the variant (``source:
"twist"``) and names the regenerate job of its sheets, which the route queues
behind the estimate gate. Every other story asks N1, byte for byte as before
(its goldens: tests/test_story_prompts_series.py, unedited).

Stdlib + pytest (DEC-012); the series tests' fixtures (the route test needs
fastapi, as every route test).
"""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from clipping.aistory import context, media_policy, prompts, schemas
from clipping.providers.pacing import estimate_tokens

import test_story_episode_steps as eps
import test_story_prompts_series as tps
import test_story_series_steps as tss_
import test_story_storyboard_props as tsp
from test_story_series_steps import hermetic, m, store, wf  # noqa: F401 - the series tests' fixtures

NOW = tss_.NOW
LATER = tss_.LATER
KIWI = eps.KIWILO
GHOST = {"char_id": KIWI, "label": "Ghost version",
         "delta_text": "a translucent pale-blue glowing ghost, feet fading into mist"}
VARIANT_CAST = [{"char_id": "char_kiwilo", "name": "Kiwilo", "variants": []},
                {"char_id": "char_mangella", "name": "Mangella", "variants": ["Reine déchue"]}]

# N1v2's prompt for the series tests' French N1 fixture and VARIANT_CAST (system, user and schema), as
# :func:`_n1v2` builds it -- recorded when the prompt was written (plan 23 stage D5).
N1V2_FR = "88bb9d97d8e86dfb42f13abe5a3e1c30f3a5bdf2211961ead716d283143c4b69"


def _sha(triple) -> str:
    return hashlib.sha256(json.dumps(list(triple), ensure_ascii=False).encode("utf-8")).hexdigest()


def _n1v2(variant_cast=VARIANT_CAST, **extra):
    kwargs = dict(memory_ep=2, arc=tps.N1_ARC_FR, cast=tps.N1_CAST_FR, memory=tps.N1_MEMORY_FR,
                  direction="plus de comédie", open_hooks=["Qui ment ?"])
    kwargs.update(extra)
    return prompts.build_n1_v2(tps._pack("fr"), variant_cast=variant_cast, **kwargs)


# ================================================================ the prompt

def test_n1v2_is_n1s_prompt_then_the_variant_block_and_its_golden():
    system, user, schema = _n1v2()
    n1_system, n1_user, _n1_schema = prompts.build_n1(
        tps._pack("fr"), memory_ep=2, arc=tps.N1_ARC_FR, cast=tps.N1_CAST_FR, memory=tps.N1_MEMORY_FR,
        direction="plus de comédie", open_hooks=["Qui ment ?"])
    assert system == n1_system and user.startswith(n1_user)
    assert user[len(n1_user):] == (
        "\n\nCharacters a twist may give an appearance variant (their variants so far):\n"
        "- char_kiwilo (Kiwilo): no variant yet\n"
        "- char_mangella (Mangella): Reine déchue\n\n"
        "A twist may also change how one existing character looks from its target episode on (a ghost version, a "
        "burned version, a disguise...). For each twist give variant: null when no one's look changes, else "
        "{char_id (one of the characters listed above), label (at most 40 characters, e.g. \"Ghost version\"), "
        "delta_text (what changes in their appearance, at most 60 words: the look only, never a name)}; at most one "
        "variant a character.")
    variant = schema["properties"]["twists"]["items"]["properties"]["variant"]
    assert variant["type"] == ["object", "null"]
    assert variant["properties"]["char_id"]["enum"] == ["char_kiwilo", "char_mangella"]
    assert "variant" in schema["properties"]["twists"]["items"]["required"]
    assert _sha((system, user, schema)) == N1V2_FR


def test_without_a_target_episode_or_a_candidate_n1v2_asks_nothing_more_than_n1():
    _s, user, schema = _n1v2(variant_cast=[])
    _s1, n1_user, _schema1 = prompts.build_n1(
        tps._pack("fr"), memory_ep=2, arc=tps.N1_ARC_FR, cast=tps.N1_CAST_FR, memory=tps.N1_MEMORY_FR,
        direction="plus de comédie", open_hooks=["Qui ment ?"])
    assert user == n1_user
    variant = schema["properties"]["twists"]["items"]["properties"]["variant"]
    assert variant["properties"]["char_id"] == {"type": "string"}


def test_the_n1_registry_rows_and_prompt_are_untouched():
    assert prompts.MAX_TOKENS["N1"] == 1430 and prompts.SCHEMA_NAMES["N1"] == "next_episode_proposals"
    assert prompts.INPUT_BUDGET["N1"] == 3740 and "N1v2" not in prompts.INPUT_BUDGET
    assert prompts.input_budget("N1v2") == prompts.VARIANTS_INPUT_BUDGET["N1v2"]
    assert prompts.TEMPERATURE["N1v2"] is prompts.IDEATION_TEMPERATURE
    assert "N1v2" not in prompts.PREMIUM_PROMPT_IDS


def _largest_n1v2_reply():
    reply = tps._largest_n1_reply()
    for number, twist in enumerate(reply["twists"]):
        twist["variant"] = {"char_id": f"char_c{number}", "label": tps._fr_chars(schemas.VARIANT_LABEL_MAX_CHARS),
                            "delta_text": tps._fr_words(schemas.VARIANT_DELTA_MAX_WORDS)}
    assert schemas.n1_v2_errors(reply, target_eps=[4, 5], variant_char_ids=["char_c0", "char_c1"]) == []
    return reply


def test_the_n1v2_cap_is_its_largest_french_reply_plus_15_percent():
    needed = estimate_tokens(json.dumps(_largest_n1v2_reply(), ensure_ascii=False)) * tps.FRENCH_TOKEN_FACTOR
    assert needed <= prompts.MAX_TOKENS["N1v2"] == -(-round(needed * 1.15, 1) // 10) * 10


def test_the_n1v2_worst_case_input_fits_its_own_budget():
    system, user, _schema = tps._n1_worst_case_input(4)
    cast = [{"char_id": f"char_c{i}", "name": tps._fr_chars(24, salt=i),
             "variants": [tps._fr_chars(schemas.VARIANT_LABEL_MAX_CHARS, salt=10 * i + k)
                          for k in range(schemas.VARIANTS_MAX - 1)]} for i in range(8)]
    user += ("\n\n" + prompts._n1_variant_block(cast) + "\n\n"
             + prompts.N1V2_VARIANTS_LINE.format(label_chars=schemas.VARIANT_LABEL_MAX_CHARS,
                                                 delta_words=schemas.VARIANT_DELTA_MAX_WORDS))
    tokens = context.check_budget(system, user, budget=prompts.input_budget("N1v2"))
    assert tokens <= prompts.input_budget("N1v2") == -(-round(tokens * 1.15, 1) // 10) * 10


def test_n1v2_errors_check_each_variant():
    reply = copy.deepcopy(tss_.N1_REPLY)
    for twist in reply["twists"]:
        twist["variant"] = None
    ids = ["char_kiwilo"]
    assert schemas.n1_v2_errors(reply, target_eps=list(range(2, 9)), variant_char_ids=ids) == []
    reply["twists"][0]["variant"] = dict(GHOST)
    assert schemas.n1_v2_errors(reply, target_eps=list(range(2, 9)), variant_char_ids=ids) == []
    for change, words in (({"char_id": "char_mangella"}, "is not one of"),
                          ({"delta_text": "brume " * 61}, "60"),
                          ({"label": "x" * 41}, "40")):
        bad = copy.deepcopy(reply)
        bad["twists"][0]["variant"].update(change)
        errors = schemas.n1_v2_errors(bad, target_eps=list(range(2, 9)), variant_char_ids=ids)
        assert any(words in error for error in errors), (change, errors)
    twice = copy.deepcopy(reply)
    twice["twists"].append(dict(twice["twists"][0], target_ep=4))
    assert any("gains a variant in another twist" in error
               for error in schemas.n1_v2_errors(twice, target_eps=list(range(2, 9)), variant_char_ids=ids))


# ================================================================ the step and the decision

def _variants_story(m, store):
    """The series tests' story with episode 1's memory approved, moved onto
    the v2 pipeline with two-view sheets, its kiwi given a look and its
    portrait on disk (what a variant's sheets are drawn from)."""
    story_id = tss_._approved_memory_story(m, store)
    store.update(story_id, lambda doc: doc["generation_profile"].update(pipeline="v2", sheet_mode="two_view",
                                                                        budget_profile="quality"), now=NOW)
    doc = store.read_entity(story_id, "characters", KIWI)
    doc["look"] = {"build": "small round kiwi body", "silhouette": "fuzzy brown oval", "face": "wide green eyes",
                   "hair": "short brown fuzz", "skin_material": "fuzzy brown kiwi skin", "height_cm": 120,
                   "palette": ["brown", "green"],
                   "wardrobe_sets": [{"id": "daily", "context": "every day", "items": "green sundress"}],
                   "season_change": None}
    store.write_entity(story_id, "characters", doc, now=NOW)
    tsp._plant_image(store, story_id, "characters", KIWI, doc["refs"]["portrait"]["name"])
    return story_id


def _n1v2_reply():
    reply = copy.deepcopy(tss_.N1_REPLY)
    reply["twists"][0]["variant"] = dict(GHOST)
    return reply


def test_a_story_without_variants_asks_n1_exactly_as_before(m, store):
    story_id = tss_._approved_memory_story(m, store)
    assert not media_policy.variants_enabled(store.get(story_id))
    llm = eps.FakeLLM(N1=[tss_.N1_REPLY])
    tss_._run(m.propose_next, store, story_id, llm=llm, step="propose-next", ep=1)
    assert llm.prompts() == ["N1"]
    assert "appearance variant" not in llm.of("N1")[0]["user"]
    assert "variant" not in llm.of("N1")[0]["schema"]["properties"]["twists"]["items"]["properties"]
    assert all("variant" not in twist for twist in tss_._proposals(store, story_id)["twists"])


def test_n1v2_proposes_a_twist_variant_and_accepting_it_creates_the_record_and_names_its_job(m, wf, store):
    story_id = _variants_story(m, store)
    llm = eps.FakeLLM(N1v2=[_n1v2_reply()])
    tss_._run(m.propose_next, store, story_id, llm=llm, step="propose-next", ep=1)
    assert llm.prompts() == ["N1v2"]
    call = llm.of("N1v2")[0]
    assert "- char_kiwilo (Kiwilo): no variant yet" in call["user"]
    doc = tss_._proposals(store, story_id)
    assert doc["twists"][0]["variant"] == GHOST
    assert schemas.next_proposals_errors(doc) == []

    approvals = copy.deepcopy(store.get(story_id)["approvals"])
    status = store.get(story_id)["status"]
    preview = wf.proposal_request(store, store.get(story_id), 2, "twist_1", accept=True)
    assert preview["variant"] == {"char_id": KIWI, "variant_id": "ghost_version", "label": "Ghost version"}
    assert preview["variant_job"] == {"step": "regenerate", "params": {
        "target": "character:char_kiwilo:variant:ghost_version", "note": None}}
    assert preview["message"].endswith(" Kiwilo gains the appearance variant 'Ghost version': its 1 variant sheet "
                                       "is made next, then approve it (variant:char_kiwilo:ghost_version).")
    assert store.read_entity(story_id, "characters", KIWI).get("variants") is None, "the preview writes nothing"

    result = wf.decide_proposal(store, story_id, 2, "twist_1", accept=True, now=LATER)
    assert result["variant_job"]["params"]["target"] == "character:char_kiwilo:variant:ghost_version"
    [variant] = store.read_entity(story_id, "characters", KIWI)["variants"]
    assert variant == {"variant_id": "ghost_version", "label": "Ghost version", "delta_text": GHOST["delta_text"],
                       "refs": {"portrait": None}, "source": "twist", "created_at": LATER, "approved_at": None}
    assert tss_._season(store, story_id)["arc"][2]["summary"] == tss_.N1_REPLY["twists"][0]["summary"]
    # RC-M5: the story's approvals and status never move.
    assert store.get(story_id)["approvals"] == approvals and store.get(story_id)["status"] == status


def test_rejecting_a_twist_variant_creates_nothing(m, wf, store):
    story_id = _variants_story(m, store)
    tss_._run(m.propose_next, store, story_id, llm=eps.FakeLLM(N1v2=[_n1v2_reply()]), step="propose-next", ep=1)
    result = wf.decide_proposal(store, story_id, 2, "twist_1", accept=False, now=LATER)
    assert "variant_job" not in result
    assert store.read_entity(story_id, "characters", KIWI).get("variants") is None


def test_the_route_queues_the_variant_sheets_behind_the_estimate_gate(m, store, tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    import test_stories_api_phase2 as p2
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import jobs, stories

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(store.outputs_dir))
    submitted = []

    async def fake_submit(job_id, payload):
        submitted.append(job_id)

    monkeypatch.setattr(worker, "submit_job", fake_submit)
    monkeypatch.setenv("DISABLE_AUTH", "1")
    story_id = _variants_story(m, store)
    tss_._run(m.propose_next, store, story_id, llm=eps.FakeLLM(N1v2=[_n1v2_reply()]), step="propose-next", ep=1)
    app = FastAPI()
    app.include_router(stories.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        url = f"/api/stories/{story_id}/episodes/2/proposals/twist_1"
        # No quality editor can run (no key): refused before anything is decided.
        monkeypatch.setattr(worker, "_settings_env", dict(p2.BASE))
        response = client.post(url, json={"accept": True})
        assert response.status_code == 409, response.text
        assert "quality" in response.text
        assert tss_._proposals(store, story_id)["decisions"] == {}
        assert store.read_entity(story_id, "characters", KIWI).get("variants") is None
        # A paid editor within the caps: decided, the variant created, its sheets queued.
        monkeypatch.setattr(worker, "_settings_env", dict(p2.BASE, FAL_KEY="test-fal-key", ALLOW_PAID="1",
                                                          DAILY_CAP_USD="4.00"))
        response = client.post(url, json={"accept": True})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["decision"] == "accepted" and body["job"]["step"] == "regenerate"
        job = job_store.get_job(body["job"]["id"])
        assert job["params"]["target"] == "character:char_kiwilo:variant:ghost_version"
        assert submitted == [job["id"]]
        assert store.read_entity(story_id, "characters", KIWI)["variants"][0]["source"] == "twist"
