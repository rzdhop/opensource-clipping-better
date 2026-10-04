"""AI Story phase 7, stage 5b: the knowledge step and the episode gate.

A v2 story writes its knowledge base (``knowledge.json``) with the
``knowledge`` step once its season is approved -- D4 the world notes, D5 one
episode's beats per call, D6 the props registry, then the ledger seed (no
call) -- each saved the moment it is accepted, so a rerun makes only the
calls still missing. The base is approved in the dashboard
(``workflow.approve_knowledge``: ``approved_at`` and ``approved_rev``), and a
v2 episode's script is refused until it is approved and current: any write
after the approval moves ``rev`` (DEC-228). A legacy story is never gated.
Deleting a character, place or prop removes it from the knowledge base and
moves ``rev``, so the base must be approved again.

Reuses ``tests/test_story_episode_steps.py``'s ready story and fakes (the
established cross-module import pattern of this suite). Stdlib + pytest
(DEC-012); the API test skips without fastapi. Offline and hermetic: no
key, chain, cap or limit of the machine reaches a test, no request leaves the
process, nothing is written outside ``tmp_path``.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from clipping.providers.errors import ProviderError

import test_story_episode_steps as eps
from test_story_episode_steps import hermetic, store  # noqa: F401 -- fixtures

NOW = eps.NOW
KEY_NAME = "Clé dorée"
KEY_ID = "prop_cle_doree"

D4 = {"geography": "Le parloir des secrets domine la piscine, de l'autre côté du jardin.",
      "period_details": "Une téléréalité tropicale d'aujourd'hui, caméras partout.",
      "visual_motifs": ["des noix de coco fêlées", "des torches au crépuscule"]}


def _d5(ep, *, new_object=False):
    objects = ["Téléphone en noix de coco"] + ([KEY_NAME] if new_object else [])
    return {"beats": [
        {"what": f"Épisode {ep} : Kiwilo cache le téléphone sous un coussin.", "place": "Le Parloir des Secrets",
         "who": ["Kiwilo", "Mangella"], "objects": objects,
         "knows_after": [{"who": "Mangella", "knows": "Kiwilo cache quelque chose."}]},
        {"what": f"Épisode {ep} : Mangella fouille la piscine.", "place": "La Piscine de la Trahison",
         "who": ["Mangella"], "objects": [], "knows_after": []},
    ]}


D6_NONE = {"keep": ["Téléphone en noix de coco"], "new_props": []}
D6_KEY = {"keep": ["Téléphone en noix de coco"],
          "new_props": [{"name": KEY_NAME, "one_line": "La clé du coffre des votes.", "owner": "Kiwilo"}]}


def _episode_of(call):
    user = call["user"]
    return next(ep for ep in range(1, 13) if f"Plan episode {ep}'s beats" in user)


def _d5_reply(*, fail_ep=None):
    """A D5 reply builder for every episode (episode 1 names a new object);
    *fail_ep*'s call fails as an unreachable chain does."""

    def reply(call):
        ep = _episode_of(call)
        if ep == fail_ep:
            return ProviderError("every link failed (test)")
        return _d5(ep, new_object=ep == 1)

    return reply


def _llm(*, d6=D6_NONE, fail_ep=None):
    return eps.FakeLLM(D4=[D4], D6=[d6], default={"D5": _d5_reply(fail_ep=fail_ep)})


def _run_knowledge(store, story_id, llm):
    from clipping.aistory.steps import knowledge

    return eps._run(knowledge, store, story_id, llm=llm, step="knowledge", ep=None)


def _fail_knowledge(store, story_id, llm):
    from clipping.aistory.steps import knowledge

    return eps._failed(knowledge, store, story_id, llm=llm, step="knowledge", ep=None)


def _refusal(store, story_id, ep=1):
    """The episode gate's refusal kind for *ep*'s script, or None."""
    from clipping.aistory.steps import episode_common

    ec = episode_common.load_context(store, story_id, ep)
    try:
        episode_common.check_episode_preconditions(None, ec, require_knowledge=True)
    except episode_common.EpisodeRefused as exc:
        return exc.kind, str(exc)
    return None


# ================================================================ the gate

def test_v2_episode_script_refused_until_knowledge_approved(store):
    from clipping.aistory import workflow
    from clipping.aistory.steps import episode_common

    story_id = eps._ready_story(store, v2=True, knowledge=False)
    planned = store.read_doc(story_id, "season.json")["episodes_planned"]

    # Missing: refused, naming the step to run -- by the rule, the API's check and the script step itself.
    kind, message = _refusal(store, story_id)
    assert kind == episode_common.KNOWLEDGE_MISSING and "run the knowledge step" in message
    with pytest.raises(workflow.WorkflowError) as refused:
        workflow.episode_context(store, store.get(story_id), 1, step="script")
    assert refused.value.code == workflow.CONFLICT
    llm = eps._script_llm()
    message, _log = eps._failed(eps._new().script, store, story_id, llm=llm)
    assert "knowledge" in message and llm.calls == []  # refused before any call
    # The storyboard and the other episode steps never meet this gate.
    workflow.episode_context(store, store.get(story_id), 1, step="storyboard")

    # Written, not approved: refused.
    llm = _llm()
    _run_knowledge(store, story_id, llm)
    assert llm.prompts() == ["D4"] + ["D5"] * planned + ["D6"]
    doc = store.read_knowledge(story_id)
    assert episode_common.knowledge_state(doc) == "draft"
    assert _refusal(store, story_id)[0] == episode_common.KNOWLEDGE_UNAPPROVED

    # Approved: allowed. The approval records the revision; the story's approvals are untouched.
    approvals = store.get(story_id)["approvals"]
    approved = workflow.approve_knowledge(store, story_id, now=NOW)
    assert approved["approved_at"] == NOW and approved["approved_rev"] == approved["rev"] == doc["rev"]
    assert store.get(story_id)["approvals"] == approvals
    assert _refusal(store, story_id) is None
    workflow.episode_context(store, store.get(story_id), 1, step="script")

    # A write after the approval: refused as stale until approved again.
    store.update_knowledge(story_id, lambda current: dict(current, world=dict(current["world"],
                                                                              visual_motifs=["la lagune"])), now=NOW)
    kind, message = _refusal(store, story_id)
    assert kind == episode_common.KNOWLEDGE_STALE and "approve it again" in message
    workflow.approve_knowledge(store, story_id, now=NOW)
    assert _refusal(store, story_id) is None


def test_legacy_story_not_gated(store):
    from clipping.aistory import workflow
    from clipping.aistory.steps import knowledge

    story_id = eps._ready_story(store)  # legacy: no pipeline, no knowledge.json
    assert store.read_knowledge(story_id) is None
    assert _refusal(store, story_id) is None
    workflow.episode_context(store, store.get(story_id), 1, step="script")

    # The knowledge step is a v2 story's alone: refused with a sentence, nothing called or written.
    with pytest.raises(workflow.WorkflowError) as refused:
        workflow.require_knowledge_runnable(store.get(story_id))
    assert refused.value.code == workflow.CONFLICT and "legacy" in str(refused.value)
    llm = _llm()
    message, _log = _fail_knowledge(store, story_id, llm)
    assert message == knowledge.LEGACY_REFUSAL and llm.calls == []
    assert store.read_knowledge(story_id) is None


# ============================================================ resumable

def test_step_resumes_after_each_call(store):
    story_id = eps._ready_story(store, v2=True, knowledge=False)
    planned = store.read_doc(story_id, "season.json")["episodes_planned"]
    assert planned > 2

    # D5 fails for episode 2: the others are written and kept, D6 waits for a complete timeline.
    first = _llm(fail_ep=2)
    message, _log = _fail_knowledge(store, story_id, first)
    assert first.prompts() == ["D4"] + ["D5"] * planned
    assert "episode 2's timeline (D5) failed" in message and "run the knowledge step again" in message
    doc = store.read_knowledge(story_id)
    assert doc["world"]["geography"] == D4["geography"]
    assert [entry["ep"] for entry in doc["timeline"]] == [1] + list(range(3, planned + 1))
    assert "props_registry" not in doc and "ledger_seed" not in doc
    assert doc["rev"] == planned  # saved after every call: D4, then each D5 written
    beat = doc["timeline"][0]["beats"][0]
    assert beat["who"] == [eps.KIWILO, eps.MANGELLA] and beat["place_id"] == eps.PARLOIR
    assert beat["objects"] == [eps.PHONE] and beat["new_objects"] == [KEY_NAME]  # kept by name for D6
    assert beat["knows_after"] == {eps.MANGELLA: "Kiwilo cache quelque chose."}

    # The rerun makes only the missing calls: episode 2 (shown episode 1's beats), then D6; then the ledger.
    second = _llm(d6=D6_KEY)
    result, log = _run_knowledge(store, story_id, second)
    assert second.prompts() == ["D5", "D6"]
    assert _episode_of(second.calls[0]) == 2 and "Episode 1's beats (already planned):" in second.calls[0]["user"]
    assert "Clé dorée (episode 1)" in second.calls[1]["user"]
    doc = store.read_knowledge(story_id)
    assert [entry["ep"] for entry in doc["timeline"]] == list(range(1, planned + 1))
    assert doc["rev"] == planned + 3  # D5, D6, the ledger seed

    # D6 created the new prop through the places step's path (no text or image yet) and registered it.
    prop = store.read_entity(story_id, "props", KEY_ID)
    assert prop["name"] == KEY_NAME and prop["owner_char_id"] == eps.KIWILO
    assert prop["descriptor"] is None and prop["image"] is None and prop["approved_at"] is None
    assert doc["props_registry"] == [eps.PHONE, KEY_ID] and result["new_props"] == [KEY_ID]
    beat = doc["timeline"][0]["beats"][0]
    assert beat["objects"] == [eps.PHONE, KEY_ID] and "new_objects" not in beat
    # The ledger seed: no look here, no location; the props each character owns.
    assert doc["ledger_seed"][eps.KIWILO] == {"location": None, "wardrobe_set": None, "possessions": [KEY_ID],
                                              "injuries": None, "relationship_notes": None}
    assert set(doc["ledger_seed"]) == {eps.KIWILO, eps.MANGELLA, eps.BROCCOLIA}
    line = next(line for line in log if "new prop(s) registered" in line)
    assert KEY_NAME in line and "next places step run" in line and "fal/seedream-4.5" in line and "$0.04" in line

    # Everything written: a third run calls nothing and writes nothing.
    third = _llm()
    _run_knowledge(store, story_id, third)
    assert third.calls == [] and store.read_knowledge(story_id)["rev"] == doc["rev"]


def test_d5_names_the_seasons_plot_archetype_and_the_episodes_beat(store):
    """Plan 20 stage 2: a season whose S1 chose plot archetypes shows D5, on
    one line, the archetype each entry plays and its beat for the entry's
    function (read-only: the season is not written by the knowledge step)."""
    from clipping.aistory import templates

    story_id = eps._ready_story(store, v2=True, knowledge=False)
    season = store.read_doc(story_id, "season.json")
    season["archetypes"] = {"primary": "rigged_contest", "secondary": None}
    for entry in season["arc"]:
        entry["archetype"] = "rigged_contest"
    store.write_doc(story_id, "season.json", season, now=NOW)
    before = store.read_doc(story_id, "season.json")
    llm = _llm()

    _run_knowledge(store, story_id, llm)

    archetype = templates.localize_archetype(templates.load_archetype("rigged_contest"), "fr")
    calls = {_episode_of(call): call["user"] for call in llm.of("D5")}
    assert sorted(calls) == list(range(1, season["episodes_planned"] + 1))
    for ep, user in calls.items():
        beat = templates.archetype_beat(archetype, season["arc"][ep - 1]["function"])
        assert beat
        assert f"Plot archetype: Le concours truqué; this episode's beat: {beat}\n\n" in user
    assert "Plot archetype" not in llm.of("D4")[0]["user"]
    assert store.read_doc(story_id, "season.json") == before


# ============================================================ deletion

def test_deleting_a_place_drops_it_from_knowledge_and_unapproves(store):
    from clipping.aistory import workflow
    from clipping.aistory.steps import episode_common

    story_id = eps._ready_story(store, v2=True, knowledge=False)
    doc = eps._approved_knowledge()
    doc["timeline"][0]["beats"].append({"what": "Mangella plonge.", "place_id": eps.PISCINE, "who": [eps.MANGELLA],
                                        "objects": [eps.PHONE], "knows_after": {}})
    planned = store.read_doc(story_id, "season.json")["episodes_planned"]
    doc["timeline"] += [{"ep": ep, "beats": doc["timeline"][0]["beats"][:1]} for ep in range(2, planned + 1)]
    doc["props_registry"] = [eps.PHONE]
    doc["ledger_seed"] = {eps.MANGELLA: {"location": eps.PISCINE, "wardrobe_set": None, "possessions": [eps.PHONE],
                                         "injuries": None, "relationship_notes": None}}
    store.write_knowledge(story_id, doc, now=NOW)
    assert _refusal(store, story_id) is None

    workflow.delete_entity(store, story_id, "places", eps.PISCINE, now=NOW)

    after = store.read_knowledge(story_id)
    assert [beat["place_id"] for beat in after["timeline"][0]["beats"]] == [eps.PARLOIR, None]
    assert after["ledger_seed"][eps.MANGELLA]["location"] is None
    assert after["rev"] == doc["rev"] + 1 and after["approved_rev"] == doc["rev"]
    assert episode_common.knowledge_state(after) == "stale"
    assert _refusal(store, story_id)[0] == episode_common.KNOWLEDGE_STALE

    # A prop too: out of every beat, the registry and the ledger; the base stays to approve again.
    workflow.delete_entity(store, story_id, "props", eps.PHONE, now=NOW)
    after = store.read_knowledge(story_id)
    assert after["timeline"][0]["beats"][1]["objects"] == [] and after["props_registry"] == []
    assert after["ledger_seed"][eps.MANGELLA]["possessions"] == []
    workflow.approve_knowledge(store, story_id, now=NOW)
    assert episode_common.knowledge_state(store.read_knowledge(story_id)) == "approved"


# ============================================================ the API

@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipping.aistory.store import StoryStore
    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import jobs, stories

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", dict(eps.SETTINGS))
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
        yield SimpleNamespace(client=client, submitted=submitted,
                              store=StoryStore(outputs, on_log=lambda line: None))


def test_api_runs_and_approves_the_knowledge_base(api):
    story_id = eps._ready_story(api.store, v2=True, knowledge=False)
    base = f"/api/stories/{story_id}"

    # GET: no knowledge yet; approving it is refused; the step queues a job.
    assert api.client.get(base).json()["knowledge"] is None
    assert api.client.post(f"{base}/approve/knowledge").status_code == 409
    assert api.client.post(f"{base}/steps/knowledge", json={"params": {"x": 1}}).status_code == 400
    queued = api.client.post(f"{base}/steps/knowledge", json={"params": {}})
    assert queued.status_code == 201, queued.text
    assert api.submitted and queued.json()["step"] == "knowledge"
    # Approving while a knowledge job is in flight (the one just queued): refused.
    busy = api.client.post(f"{base}/approve/knowledge")
    assert busy.status_code == 409 and "is queued" in busy.text
    from web.api import store as job_store

    job_store._jobs.clear()

    doc = eps._approved_knowledge()
    doc.pop("approved_rev")
    doc["approved_at"] = None
    api.store.write_knowledge(story_id, doc, now=NOW)
    # The season plans 8 episodes and this timeline has one: not complete, refused.
    refused = api.client.post(f"{base}/approve/knowledge")
    assert refused.status_code == 409 and "timeline of episode(s) 2" in refused.text

    planned = api.store.read_doc(story_id, "season.json")["episodes_planned"]
    beat = doc["timeline"][0]["beats"][0]
    doc["timeline"] = [{"ep": ep, "beats": [beat]} for ep in range(1, planned + 1)]
    api.store.write_knowledge(story_id, doc, now=NOW)
    approved = api.client.post(f"{base}/approve/knowledge")
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["approved_at"] and body["approved_rev"] == body["rev"]
    payload = api.client.get(base).json()
    assert payload["knowledge"]["approved_rev"] == payload["knowledge"]["rev"]
    assert payload["story"]["approvals"]["season"]  # the story's own approvals untouched

    # A legacy story: the step is refused before any job exists.
    legacy = eps._ready_story(api.store)
    assert api.client.post(f"/api/stories/{legacy}/steps/knowledge", json={"params": {}}).status_code == 409

