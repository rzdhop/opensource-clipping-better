"""The AI Story API, steps 1-4 (phase 1, stage 7; spec 3, 9.1, 9.2).

``web/api/routes/stories.py`` on a throwaway app -- the stories router and the
jobs router, never the singleton ``web.api.app.app`` -- with the job store on
an empty table persisted under ``tmp_path``, ``worker.OUTPUTS_ROOT`` under
``tmp_path`` and ``worker.submit_job`` replaced by a recorder. A step that has
to *run* goes through the real worker path (``worker._execute_story_step``)
with the registered runner answered by a stand-in for ``llm.run_chain``: no
network, no real key (the Settings values below are test values).

The two text guards at the top run in the pytest-only CI environment
(DEC-012); everything else needs pydantic, fastapi and httpx and skips
without them, like the other route tests. The new modules are imported inside
the fixtures and tests, so against the parent commit every group fails on its
own rather than the whole file failing to collect.
"""

from __future__ import annotations

import copy
import importlib
import json
import pathlib
from types import SimpleNamespace

import pytest

from clipping.aistory import defaults, schemas, templates
from clipping.cancel import CancelToken
from clipping.providers.errors import ProviderError
from clipping.providers.registry import Link

ROOT = pathlib.Path(__file__).resolve().parents[1]
MODELS = ROOT / "web" / "api" / "models.py"
APP = ROOT / "web" / "api" / "app.py"
APP_ROUTES = ROOT / "web" / "api" / "routes" / "stories.py"

# Test values only: submit_job is replaced and every step run here is answered
# by FakeRunner, so the key never leaves the process.
LLM_SETTINGS = {"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key"}
LINK = Link("gemini", "gemini-test")

TENTAFRUIT = next(c for c in templates.load_concepts() if c["concept_id"] == "tentafruit_island")
UNKNOWN_ID = "0123456789ab"


# ------------------------------------------------------------ text guards (CI)

def test_the_generation_profile_defaults_are_written_as_the_agreement_test_reads_them():
    text = MODELS.read_text(encoding="utf-8")
    body = text[text.index("class GenerationProfileModel"):]
    body = body[: body.index("\nclass ")]
    for line in (
        '    tier: int = 1\n',
        '    route: Literal["auto","local","api"] = "auto"\n',
        '    consistency_mode: Literal["references","prompt_only"] = "references"\n',
        '    budget_profile: Literal["free","one_dollar","quality"] = "free"\n',
    ):
        assert line in body, line


def test_the_stories_router_is_included_before_the_dashboard_mount():
    text = APP.read_text(encoding="utf-8")
    assert "app.include_router(stories.router)" in text
    assert text.index("app.include_router(stories.router)") < text.index("app.mount(")


# ---------------------------------------------------------------- fixtures

class FakeRunner:
    """Stands in for ``llm.run_chain``: records each call, answers from a queue
    (a reply, or an exception to raise)."""

    def __init__(self, *replies):
        self.queue = list(replies)
        self.calls = []

    def __call__(self, chain, **kwargs):
        self.calls.append(dict(kwargs, chain=list(chain)))
        if not self.queue:
            raise AssertionError(f"no reply queued for call {len(self.calls)}")
        reply = self.queue.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return copy.deepcopy(reply), LINK


@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env
    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import jobs, stories

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "ALLOW_SLOW_CHAIN", "MAX_QUEUED_JOBS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", dict(LLM_SETTINGS))
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker, "UPLOADS_ROOT", str(tmp_path / "uploads"))

    submitted = []

    async def fake_submit(job_id, payload):
        submitted.append((job_id, payload))

    monkeypatch.setattr(worker, "submit_job", fake_submit)
    monkeypatch.setenv("DISABLE_AUTH", "1")

    app = FastAPI()
    app.include_router(stories.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, jobs=job_store, worker=worker, outputs=outputs,
            submitted=submitted, monkeypatch=monkeypatch, tmp_path=tmp_path,
        )


def _settings(api, env):
    api.monkeypatch.setattr(api.worker, "_settings_env", dict(env))


def _create(api, language="fr", **body):
    response = api.client.post("/api/stories", json={"language": language, **body})
    assert response.status_code == 201, response.text
    return response.json()


def _chosen(api, language="fr", concept_id="tentafruit_island", **body):
    story_id = _create(api, language, **body)["story_id"]
    response = api.client.post(f"/api/stories/{story_id}/concepts/choose", json={"concept_id": concept_id})
    assert response.status_code == 200, response.text
    return story_id


def _with_bible(api, *, approve=True):
    """A story with ``tentafruit_island`` chosen and every bible field written."""
    story_id = _chosen(api)
    response = api.client.patch(f"/api/stories/{story_id}", json=BIBLE_PATCH)
    assert response.status_code == 200, response.text
    if approve:
        response = api.client.post(f"/api/stories/{story_id}/approve/bible")
        assert response.status_code == 200, response.text
    return story_id


def _story_store(api):
    from clipping.aistory.store import StoryStore

    return StoryStore(str(api.outputs), on_log=lambda line: None)


def _job(api, story_id, step, status=None, params=None):
    """A step job straight in the store (for the states the API only reaches
    by running the worker)."""
    from web.api.models import JobStatus

    job_id = api.jobs.create_job(kind="story_step", story_id=story_id, step=step, params=params)
    if status is not None:
        api.jobs.set_status(job_id, JobStatus(status))
    return job_id


def _status(api, job_id):
    return api.jobs.get_job(job_id)["status"]


def _run(api, job_id, *replies):
    """Run a queued step job the way the worker does, its LLM answered by *replies*."""
    from clipping.aistory import steps

    job = api.jobs.get_job(job_id)
    module = importlib.import_module(f"clipping.aistory.steps.{job['step']}")
    runner = FakeRunner(*replies)
    api.monkeypatch.setitem(steps.RUNNERS, job["step"], lambda ctx: module.run(ctx, runner=runner))
    api.worker._execute_story_step(job_id, job, CancelToken())
    return runner


# ----------------------------------------------------------------- replies

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
BIBLE_PATCH = {**B1_REPLY, "world": B2_REPLY, **B3_REPLY}
B2_OUTAGE = ProviderError("x", failures=[("gemini/gemini-test", "APITimeoutError: timed out")])


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


def _c1_reply(batch):
    first = 2 * batch - 1
    return {"concepts": [_c1_concept(f"Titre {first}"), _c1_concept(f"Titre {first + 1}", "anime")]}


C1_REPLIES = [_c1_reply(k) for k in range(1, 6)]


# ============================================================ the whole path

def test_create_choose_bible_approve_style_approve(api):
    c = api.client

    # Step 1: a draft.
    response = c.post("/api/stories", json={"language": "fr", "seed_text": "Des fruits sur une île."})
    assert response.status_code == 201
    story = response.json()
    story_id = story["story_id"]
    assert (story["status"], story["language"], story["seed_text"]) == ("draft", "fr", "Des fruits sur une île.")
    assert story["generation_profile"] == defaults.default_generation_profile()
    assert [e["story_id"] for e in c.get("/api/stories").json()["stories"]] == [story_id]

    # Step 2: the library in French, the fruit_drama ones, then a choice.
    body = c.get(f"/api/stories/{story_id}/concepts", params={"style": "fruit_drama"}).json()
    assert [card["concept_id"] for card in body["library"]] == [
        "midnight_fridge", "orchard_inheritance", "tentafruit_island"]
    card = body["library"][-1]
    assert card["title"] == TENTAFRUIT["title"]["fr"]
    assert card["logline"] == TENTAFRUIT["logline"]["fr"]
    assert body["generated"] == []

    response = c.post(f"/api/stories/{story_id}/concepts/choose", json={"concept_id": "tentafruit_island"})
    assert response.status_code == 200
    story = response.json()
    assert (story["status"], story["concept_id"], story["title"]) == (
        "concept_chosen", "tentafruit_island", TENTAFRUIT["title"]["fr"])
    assert story["concept"]["world"] == TENTAFRUIT["world"]["fr"]

    # Step 3: the bible is a job.
    response = c.post(f"/api/stories/{story_id}/steps/bible", json={})
    assert response.status_code == 201
    job = response.json()
    assert (job["kind"], job["story_id"], job["step"], job["status"]) == (
        "story_step", story_id, "bible", "queued")
    assert api.submitted == [(job["id"], {})]

    # Not approvable while it is being written...
    response = c.post(f"/api/stories/{story_id}/approve/bible")
    assert response.status_code == 409
    assert job["id"] in response.json()["detail"]

    # ...and not while a field is missing: B2 fails, B1 and B3 are kept.
    _run(api, job["id"], B1_REPLY, B2_OUTAGE, B3_REPLY)
    assert _status(api, job["id"]) == "failed"
    response = c.post(f"/api/stories/{story_id}/approve/bible")
    assert response.status_code == 409
    detail = response.json()["detail"]
    assert "world" in detail
    for present in ("logline", "premise", "tone", "genre_tags", "themes_and_values", "audience", "why_come_back"):
        assert present not in detail

    # The missing field regenerated, with a note that reaches the prompt.
    response = c.post(f"/api/stories/{story_id}/regenerate", json={"target": "bible:world", "note": "plus sombre"})
    assert response.status_code == 201
    regen = response.json()
    assert (regen["step"], regen["params"]) == ("regenerate", {"target": "bible:world", "note": "plus sombre"})
    runner = _run(api, regen["id"], B2_REPLY)
    assert "plus sombre" in runner.calls[0]["user"]
    assert _status(api, regen["id"]) == "awaiting_approval"

    response = c.post(f"/api/stories/{story_id}/approve/bible")
    assert response.status_code == 200
    assert response.json()["status"] == "bible_approved"
    record = api.jobs.get_job(regen["id"])
    assert record["status"] == "completed" and record["approved_at"]

    # Step 4: the style runs here, no job.
    response = c.post(f"/api/stories/{story_id}/steps/style",
                      json={"params": {"overrides": {"palette.accents": ["#FFD400"]}}})
    assert response.status_code == 200
    body = response.json()
    lock = body["style_lock"]
    assert lock["template_id"] == "fruit_drama"  # the concept's style
    assert lock["palette"]["accents"] == ["#FFD400"]
    assert lock["overrides"] == {"palette.accents": ["#FFD400"]}
    assert lock["locked_at"] is None
    assert body["story"]["style_template_id"] == "fruit_drama"
    assert len(api.submitted) == 2
    on_disk = json.loads((api.outputs / "stories" / story_id / "style_lock.json").read_text())
    assert schemas.style_lock_errors(on_disk) == []

    response = c.post(f"/api/stories/{story_id}/approve/style")
    assert response.status_code == 200
    assert response.json()["status"] == "style_approved"

    # Locked: the style step no longer changes it.
    response = c.post(f"/api/stories/{story_id}/steps/style",
                      json={"params": {"overrides": {"palette.accents": ["#000000"]}}})
    assert response.status_code == 409
    assert "locked" in response.json()["detail"]

    full = c.get(f"/api/stories/{story_id}").json()
    assert full["story"]["status"] == "style_approved"
    assert full["style_lock"]["locked_at"] and full["style_lock"]["palette"]["accents"] == ["#FFD400"]
    assert (full["concepts_generated"], full["cost_total_usd"], full["route"]) == (0, 0.0, "auto")
    assert [(j["id"], j["status"]) for j in full["jobs"]] == [
        (job["id"], "failed"), (regen["id"], "completed")]


def test_generated_concepts_are_listed_and_chosen(api):
    story_id = _create(api)["story_id"]
    job = api.client.post(f"/api/stories/{story_id}/concepts/generate").json()
    assert job["step"] == "concepts"
    _run(api, job["id"], *C1_REPLIES)
    assert _status(api, job["id"]) == "awaiting_approval"

    body = api.client.get(f"/api/stories/{story_id}/concepts").json()
    generated = body["generated"]
    assert [card["concept_id"] for card in generated] == [f"gen_{n:02d}" for n in range(1, 11)]
    assert api.client.get(f"/api/stories/{story_id}").json()["concepts_generated"] == 10

    response = api.client.post(f"/api/stories/{story_id}/concepts/choose", json={"concept_id": "gen_03"})
    assert response.status_code == 200
    story = response.json()
    assert story["concept_id"] == "custom"
    assert story["concept"]["concept_id"] == "gen_03"
    assert story["title"] == generated[2]["title"]
    assert story["status"] == "concept_chosen"

    # The choice is the concepts job's approval.
    record = api.jobs.get_job(job["id"])
    assert record["status"] == "completed" and record["approved_at"]


# =================================================================== key gate

@pytest.mark.parametrize("path,body", [
    ("concepts/generate", None),
    ("steps/concepts", {}),
    ("steps/bible", {}),
    ("regenerate", {"target": "bible:tone"}),
    ("regenerate", {"target": "concepts"}),
])
def test_a_chain_with_no_key_is_refused_before_any_job_exists(api, path, body):
    story_id = _chosen(api)
    _settings(api, {"LLM_CHAIN": "gemini/gemini-test"})

    response = api.client.post(f"/api/stories/{story_id}/{path}", json=body)

    assert response.status_code == 400
    assert "GOOGLE_API_KEY" in response.json()["detail"]
    assert api.jobs.list_jobs() == [] and api.submitted == []


def test_the_no_key_refusal_names_the_keys_of_the_default_chain(api):
    from clipping.providers import registry

    story_id = _create(api)["story_id"]
    _settings(api, {})

    detail = api.client.post(f"/api/stories/{story_id}/concepts/generate").json()["detail"]

    for name in ("groq", "gemini", "openrouter", "mistral"):
        assert registry.PROVIDERS[name].env_key in detail
    assert "OPENROUTER_API_KEY (https://openrouter.ai/keys) (paid)" in detail


def test_the_slow_floor_alone_is_refused_exactly_as_a_clip_job_is(api):
    """One rule, one function: the story step and POST /api/jobs give the same
    refusal for the same Settings."""
    story_id = _create(api)["story_id"]
    _settings(api, {"NVIDIA_API_KEY": "test-nvidia-key"})

    story_response = api.client.post(f"/api/stories/{story_id}/concepts/generate")
    clip_response = api.client.post("/api/jobs", json={"upload_filename": "talk.mp4"})

    assert story_response.status_code == clip_response.status_code == 400
    assert story_response.json()["detail"] == clip_response.json()["detail"]
    assert "GROQ_API_KEY" in story_response.json()["detail"]
    assert api.jobs.list_jobs() == [] and api.submitted == []


def test_the_slow_chain_switch_lets_a_story_step_through(api):
    story_id = _create(api)["story_id"]
    _settings(api, {"NVIDIA_API_KEY": "test-nvidia-key", "ALLOW_SLOW_CHAIN": "1"})

    assert api.client.post(f"/api/stories/{story_id}/concepts/generate").status_code == 201


def test_one_step_at_a_time_per_story_but_not_across_stories(api):
    story_id = _chosen(api)
    other_id = _create(api)["story_id"]

    first = api.client.post(f"/api/stories/{story_id}/concepts/generate").json()
    for path, body in (("steps/bible", {}), ("steps/concepts", {}), ("regenerate", {"target": "bible:tone"})):
        response = api.client.post(f"/api/stories/{story_id}/{path}", json=body)
        assert response.status_code == 409, path
        assert first["id"] in response.json()["detail"] and "concepts" in response.json()["detail"]

    api.jobs.set_status(first["id"], api.jobs.JobStatus.RUNNING)
    assert api.client.post(f"/api/stories/{story_id}/steps/bible", json={}).status_code == 409

    assert api.client.post(f"/api/stories/{other_id}/concepts/generate").status_code == 201

    # Awaiting approval is finished for the worker: the story may move on.
    api.jobs.set_status(first["id"], api.jobs.JobStatus.AWAITING_APPROVAL)
    assert api.client.post(f"/api/stories/{story_id}/steps/bible", json={}).status_code == 201
    assert len(api.submitted) == 3


def test_a_cancelled_step_still_counts_until_its_worker_has_stopped(api):
    # A cancel is marked at once, but a request already in flight finishes
    # first (DEC-075) and the runner may still write its reply.
    story_id = _chosen(api)
    job_id = _job(api, story_id, "bible", "cancelled")
    api.monkeypatch.setitem(api.worker._active, job_id, (CancelToken(), None))

    for method, path, body in (("POST", "steps/concepts", {}), ("PATCH", "", {"title": "x"}),
                               ("DELETE", "", None), ("POST", "approve/bible", None)):
        response = api.client.request(method, f"/api/stories/{story_id}/{path}".rstrip("/"), json=body)
        assert response.status_code == 409, (method, path)
        assert job_id in response.json()["detail"]

    api.worker._active.pop(job_id)
    assert api.client.post(f"/api/stories/{story_id}/steps/concepts", json={}).status_code == 201


def test_a_full_queue_is_refused_with_429_and_creates_nothing(api):
    api.monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    api.jobs.create_job(upload_filename="talk.mp4")  # a clip job waiting
    story_id = _create(api)["story_id"]

    response = api.client.post(f"/api/stories/{story_id}/concepts/generate")

    assert response.status_code == 429
    assert "MAX_QUEUED_JOBS=1" in response.json()["detail"]
    assert api.jobs.list_step_jobs(story_id) == [] and api.submitted == []


# ============================================================== superseding

def test_a_second_bible_job_supersedes_the_awaiting_first(api):
    story_id = _chosen(api)
    first = api.client.post(f"/api/stories/{story_id}/steps/bible", json={}).json()
    api.jobs.set_status(first["id"], api.jobs.JobStatus.AWAITING_APPROVAL)

    second = api.client.post(f"/api/stories/{story_id}/steps/bible", json={}).json()

    record = api.jobs.get_job(first["id"])
    assert record["status"] == "completed"
    assert record["superseded_by"] == second["id"]
    assert record.get("approved_at") is None
    assert _status(api, second["id"]) == "queued"


def test_a_regenerated_field_supersedes_the_awaiting_bible_but_not_the_concepts(api):
    story_id = _chosen(api)
    concepts_job = _job(api, story_id, "concepts", "awaiting_approval")
    bible_job = _job(api, story_id, "bible", "awaiting_approval")

    regen = api.client.post(f"/api/stories/{story_id}/regenerate", json={"target": "bible:tone"}).json()

    assert api.jobs.get_job(bible_job)["superseded_by"] == regen["id"]
    assert _status(api, concepts_job) == "awaiting_approval"


def test_choosing_a_concept_completes_only_the_awaiting_concepts_jobs(api):
    story_id = _chosen(api)
    concepts_job = _job(api, story_id, "concepts", "awaiting_approval")
    more = _job(api, story_id, "regenerate", "awaiting_approval", params={"target": "concepts"})
    bible_job = _job(api, story_id, "bible", "awaiting_approval")

    response = api.client.post(f"/api/stories/{story_id}/concepts/choose", json={"concept_id": "midnight_fridge"})

    assert response.status_code == 200
    for job_id in (concepts_job, more):
        assert _status(api, job_id) == "completed" and api.jobs.get_job(job_id)["approved_at"]
    assert _status(api, bible_job) == "awaiting_approval"


def test_approving_the_bible_completes_the_awaiting_bible_and_regenerate_jobs(api):
    story_id = _with_bible(api, approve=False)
    bible_job = _job(api, story_id, "bible", "awaiting_approval")
    regen_job = _job(api, story_id, "regenerate", "awaiting_approval", params={"target": "bible:tone"})
    concepts_job = _job(api, story_id, "concepts", "awaiting_approval")

    assert api.client.post(f"/api/stories/{story_id}/approve/bible").status_code == 200

    for job_id in (bible_job, regen_job):
        assert _status(api, job_id) == "completed" and api.jobs.get_job(job_id)["approved_at"]
    assert _status(api, concepts_job) == "awaiting_approval"


def test_an_awaiting_bible_job_survives_a_restart_and_is_still_approved(api):
    story_id = _with_bible(api, approve=False)
    job = api.client.post(f"/api/stories/{story_id}/steps/bible", json={}).json()
    api.jobs.set_status(job["id"], api.jobs.JobStatus.AWAITING_APPROVAL)
    running = _job(api, _create(api)["story_id"], "concepts", "running")

    # The restart: a fresh table, read back from jobs.json, then the startup sweep.
    api.monkeypatch.setattr(api.jobs, "_jobs", {})
    api.jobs._load()
    api.jobs.fail_stale_jobs()
    assert _status(api, job["id"]) == "awaiting_approval"
    assert _status(api, running) == "failed"

    response = api.client.post(f"/api/stories/{story_id}/approve/bible")

    assert response.status_code == 200
    assert response.json()["status"] == "bible_approved"
    record = api.jobs.get_job(job["id"])
    assert record["status"] == "completed" and record["approved_at"]


# =================================================================== delete

@pytest.mark.parametrize("status", ["queued", "running"])
def test_a_story_with_a_step_in_flight_is_not_deleted(api, status):
    story_id = _create(api)["story_id"]
    job_id = _job(api, story_id, "concepts", status)

    response = api.client.delete(f"/api/stories/{story_id}")

    assert response.status_code == 409
    detail = response.json()["detail"]
    assert job_id in detail and "concepts" in detail and "cancel it first" in detail
    assert (api.outputs / "stories" / story_id / "story.json").is_file()
    assert [e["story_id"] for e in api.client.get("/api/stories").json()["stories"]] == [story_id]
    assert api.jobs.get_job(job_id) is not None


def test_delete_removes_the_folder_the_index_entry_and_the_step_jobs_only(api):
    story_id = _create(api)["story_id"]
    sibling_id = _create(api, "en")["story_id"]
    doomed = [_job(api, story_id, "concepts", "awaiting_approval"), _job(api, story_id, "bible", "failed")]
    sibling_job = _job(api, sibling_id, "concepts", "awaiting_approval")
    clip_job = api.jobs.create_job(upload_filename="talk.mp4")

    response = api.client.delete(f"/api/stories/{story_id}")

    assert response.status_code == 200
    assert response.json() == {
        "message": "Story deleted", "id": story_id,
        "removed": [f"outputs/stories/{story_id}/"], "kept": [], "jobs_removed": 2,
    }
    assert not (api.outputs / "stories" / story_id).exists()
    assert [e["story_id"] for e in api.client.get("/api/stories").json()["stories"]] == [sibling_id]
    assert all(api.jobs.get_job(job_id) is None for job_id in doomed)
    assert api.client.get(f"/api/stories/{story_id}").status_code == 404

    assert (api.outputs / "stories" / sibling_id / "story.json").is_file()
    assert api.client.get(f"/api/stories/{sibling_id}").status_code == 200
    assert api.jobs.get_job(sibling_job) is not None and api.jobs.get_job(clip_job) is not None


def test_a_corrupt_story_is_a_short_500_and_can_still_be_deleted(api):
    story_id = _create(api)["story_id"]
    (api.outputs / "stories" / story_id / "story.json").write_text('{"not": "a story"}')

    response = api.client.get(f"/api/stories/{story_id}")

    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail.startswith(f"Story {story_id} cannot be read")
    assert "Traceback" not in detail and len(detail) < 400

    assert api.client.delete(f"/api/stories/{story_id}").status_code == 200
    assert not (api.outputs / "stories" / story_id).exists()


def test_an_index_entry_whose_folder_is_gone_can_be_deleted(api):
    import shutil

    story_id = _create(api)["story_id"]
    shutil.rmtree(api.outputs / "stories" / story_id)

    response = api.client.delete(f"/api/stories/{story_id}")

    assert response.status_code == 200
    assert response.json()["removed"] == []
    assert api.client.get("/api/stories").json()["stories"] == []


# ================================================================== bad ids

ID_ROUTES = [
    ("GET", "", None),
    ("PATCH", "", {"title": "x"}),
    ("DELETE", "", None),
    ("GET", "/concepts", None),
    ("POST", "/concepts/generate", None),
    ("POST", "/concepts/choose", {"concept_id": "tentafruit_island"}),
    ("POST", "/steps/concepts", {}),
    ("POST", "/steps/style", {}),
    ("POST", "/approve/bible", None),
    ("POST", "/regenerate", {"target": "concepts"}),
    ("GET", "/estimate/concepts", None),
]


@pytest.mark.parametrize("bad_id", ["ABC", "0123456789AB", UNKNOWN_ID, "0123456789abc", "..%2Fx", "%2E%2E"])
@pytest.mark.parametrize("method,suffix,body", ID_ROUTES)
def test_a_bad_or_unknown_id_is_a_404_everywhere(api, bad_id, method, suffix, body):
    other_id = _create(api)["story_id"]

    response = api.client.request(method, f"/api/stories/{bad_id}{suffix}", json=body)

    assert response.status_code == 404
    assert api.jobs.list_jobs() == [] and api.submitted == []
    assert sorted(p.name for p in (api.outputs / "stories").iterdir()) == [other_id]
    assert sorted(p.name for p in api.outputs.iterdir()) == ["stories", "stories.json"]


# ============================================================ grammar edges

LATER_STEPS = ["cast", "places", "season", "script", "storyboard", "assets", "render", "metadata",
               "memory", "feedback", "propose-next", "rerender", "fast-track", "import"]


@pytest.mark.parametrize("step", LATER_STEPS)
def test_a_later_phase_step_is_a_400(api, step):
    story_id = _with_bible(api)
    response = api.client.post(f"/api/stories/{story_id}/steps/{step}", json={})
    assert response.status_code == 400
    assert response.json()["detail"] == f"'{step}' arrives in a later phase."
    assert api.jobs.list_jobs() == []


@pytest.mark.parametrize("doc", ["character:char_kiwilo", "place:place_beach", "prop:prop_phone", "season",
                                 "script:1", "storyboard:1", "assets:1"])
def test_a_later_phase_approval_is_a_400(api, doc):
    story_id = _with_bible(api)
    response = api.client.post(f"/api/stories/{story_id}/approve/{doc}")
    assert response.status_code == 400
    assert "later phase" in response.json()["detail"]


@pytest.mark.parametrize("target", [
    "character:char_kiwilo:text", "character:char_kiwilo:image:portrait", "place:place_beach:text",
    "prop:prop_phone:image", "season:1", "scene:1:s02", "hook:1", "cliffhanger:1", "teaser:1",
    "shot:1:sh03", "shot:1:sh03:video", "line:1:l04", "metadata:1:tiktok",
])
def test_a_later_phase_regenerate_target_is_a_400(api, target):
    story_id = _with_bible(api)
    response = api.client.post(f"/api/stories/{story_id}/regenerate", json={"target": target})
    assert response.status_code == 400
    assert "later phase" in response.json()["detail"]
    assert api.jobs.list_jobs() == []


@pytest.mark.parametrize("target", ["nope", "bible", "bible:nope", "bible:genre_tags", "concepts:1", ""])
def test_an_unknown_regenerate_target_is_a_400_naming_the_valid_ones(api, target):
    story_id = _with_bible(api)
    response = api.client.post(f"/api/stories/{story_id}/regenerate", json={"target": target})
    assert response.status_code == 400
    detail = response.json()["detail"]
    for valid in ("bible:logline", "bible:premise", "bible:tone", "bible:world", "bible:themes", "concepts"):
        assert valid in detail


def test_unknown_steps_and_documents_are_404s_and_the_preview_needs_a_style(api):
    story_id = _with_bible(api)
    c = api.client
    assert c.post(f"/api/stories/{story_id}/steps/nope", json={}).status_code == 404
    # Regenerating has its own route.
    assert c.post(f"/api/stories/{story_id}/steps/regenerate", json={}).status_code == 404
    assert c.post(f"/api/stories/{story_id}/approve/nope").status_code == 404
    assert c.post(f"/api/stories/{story_id}/approve/concept").status_code == 404

    # Stage 8: the preview exists, and needs the style lock this story lacks.
    response = c.post(f"/api/stories/{story_id}/steps/style_preview", json={})
    assert response.status_code == 409 and response.json()["detail"] == "Build the style first."
    assert api.jobs.list_jobs() == []


def test_the_bible_and_its_regeneration_need_a_chosen_concept(api):
    story_id = _create(api)["story_id"]
    for path, body in (("steps/bible", {}), ("regenerate", {"target": "bible:logline"})):
        response = api.client.post(f"/api/stories/{story_id}/{path}", json=body)
        assert response.status_code == 409
        assert response.json()["detail"] == "Choose a concept first."
    # Concepts are how one gets chosen.
    assert api.client.post(f"/api/stories/{story_id}/regenerate", json={"target": "concepts"}).status_code == 201


# =================================================================== create

def test_a_story_without_a_language_is_a_422_and_nothing_is_created(api):
    response = api.client.post("/api/stories", json={"seed_text": "x"})
    assert response.status_code == 422
    assert api.client.get("/api/stories").json()["stories"] == []
    assert not (api.outputs / "stories").exists()


def test_an_unknown_style_template_is_a_400_naming_the_shipped_ones(api):
    response = api.client.post("/api/stories", json={"language": "en", "style_template_id": "vaporwave"})
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert "vaporwave" in detail and "fruit_drama" in detail and "claymation" in detail


def test_a_generation_profile_is_taken_at_creation(api):
    story = _create(api, "en", generation_profile={"route": "local", "tier": 2})
    assert story["generation_profile"] == {
        "tier": 2, "route": "local", "consistency_mode": "references", "budget_profile": "free"}
    response = api.client.post("/api/stories", json={"language": "en", "generation_profile": {"route": "cloud"}})
    assert response.status_code == 422


# ==================================================================== models

def test_the_generation_profile_model_defaults_are_the_story_defaults():
    pytest.importorskip("pydantic")
    from web.api.models import GenerationProfileModel

    assert GenerationProfileModel().model_dump() == defaults.default_generation_profile()


def test_a_create_request_has_no_default_language():
    pydantic = pytest.importorskip("pydantic")
    from web.api.models import StoryCreateRequest

    with pytest.raises(pydantic.ValidationError):
        StoryCreateRequest()
    assert StoryCreateRequest(language="fr").language == "fr"


# ===================================================================== patch

def test_a_bible_field_clears_the_bible_approval_and_a_title_does_not(api):
    story_id = _with_bible(api)

    story = api.client.patch(f"/api/stories/{story_id}", json={"title": "Nouveau titre"}).json()
    assert story["title"] == "Nouveau titre"
    assert story["approvals"]["bible"] and story["status"] == "bible_approved"

    story = api.client.patch(f"/api/stories/{story_id}", json={
        "seed_text": "autre", "narrator": {"enabled": True}, "generation_profile": {"route": "api"},
    }).json()
    assert story["approvals"]["bible"] and story["status"] == "bible_approved"
    assert story["narrator"] == {"enabled": True, "voice": None}
    assert story["generation_profile"] == {**defaults.default_generation_profile(), "route": "api"}

    story = api.client.patch(f"/api/stories/{story_id}", json={"tone": "sombre et rapide"}).json()
    assert story["tone"] == "sombre et rapide"
    assert story["approvals"]["bible"] is None and story["status"] == "concept_chosen"


def test_only_the_fields_sent_are_touched(api):
    story_id = _with_bible(api)
    before = api.client.get(f"/api/stories/{story_id}").json()["story"]

    after = api.client.patch(f"/api/stories/{story_id}", json={"title": "x"}).json()

    changed = {key for key in before if before[key] != after[key]}
    assert changed == {"title", "updated_at"}


def test_a_null_clears_a_nullable_field_and_is_refused_for_the_others(api):
    story_id = _with_bible(api)

    story = api.client.patch(f"/api/stories/{story_id}", json={"logline": None}).json()
    assert story["logline"] is None and story["approvals"]["bible"] is None

    response = api.client.patch(f"/api/stories/{story_id}", json={"genre_tags": None})
    assert response.status_code == 400
    assert any("genre_tags" in error for error in response.json()["detail"]["errors"])


@pytest.mark.parametrize("profile", [
    {"route": "cloud"}, {"tier": 4}, {"tier": True}, {"budget_profile": "ten_dollars"}, {"speed": "fast"},
])
def test_a_bad_generation_profile_is_a_400_and_changes_nothing(api, profile):
    story_id = _create(api)["story_id"]
    before = api.client.get(f"/api/stories/{story_id}").json()["story"]

    response = api.client.patch(f"/api/stories/{story_id}", json={"generation_profile": profile})

    assert response.status_code == 400
    assert "generation_profile" in response.json()["detail"]
    assert api.client.get(f"/api/stories/{story_id}").json()["story"] == before


def test_a_value_the_story_schema_refuses_is_a_400_with_its_errors(api):
    story_id = _create(api)["story_id"]
    response = api.client.patch(f"/api/stories/{story_id}", json={"genre_tags": [f"t{i}" for i in range(9)]})
    assert response.status_code == 400
    assert any("genre_tags" in error for error in response.json()["detail"]["errors"])


def test_a_patch_while_a_step_is_in_flight_is_a_409(api):
    story_id = _chosen(api)
    job = api.client.post(f"/api/stories/{story_id}/steps/bible", json={}).json()

    response = api.client.patch(f"/api/stories/{story_id}", json={"logline": "x"})

    assert response.status_code == 409
    assert job["id"] in response.json()["detail"]
    assert api.client.get(f"/api/stories/{story_id}").json()["story"]["logline"] is None


# ================================================================= concepts

def test_the_library_follows_the_language_asked_for(api):
    story_id = _create(api, "fr")["story_id"]
    body = api.client.get(f"/api/stories/{story_id}/concepts", params={"language": "en"}).json()
    card = next(c for c in body["library"] if c["concept_id"] == "tentafruit_island")
    assert card["title"] == TENTAFRUIT["title"]["en"]
    assert len(body["library"]) == len(templates.load_concepts())


@pytest.mark.parametrize("params", [{"language": "de"}, {"style": "vaporwave"}, {"style": "../styles"}])
def test_a_bad_language_or_style_is_a_400(api, params):
    story_id = _create(api)["story_id"]
    assert api.client.get(f"/api/stories/{story_id}/concepts", params=params).status_code == 400


def test_a_custom_concept_is_checked_with_the_card_rules(api):
    story_id = _create(api)["story_id"]
    concept = _c1_concept("Mon concept à moi")

    bad = dict(concept, cast_sketch=concept["cast_sketch"][:2])
    del bad["hook_formula"]
    response = api.client.post(f"/api/stories/{story_id}/concepts/choose", json={"concept": bad})
    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert any("hook_formula" in e for e in errors) and any("cast_sketch" in e for e in errors)

    # A generated card sent back as it came is accepted: its bookkeeping is replaced.
    sent = dict(concept, concept_id="gen_07", source="generated", prompt_version="s1")
    response = api.client.post(f"/api/stories/{story_id}/concepts/choose", json={"concept": sent})
    assert response.status_code == 200
    story = response.json()
    assert story["concept_id"] == "custom" and story["concept"]["concept_id"] == "custom"
    assert story["concept"]["language"] == "fr"
    assert story["title"] == "Mon concept à moi" and story["status"] == "concept_chosen"


@pytest.mark.parametrize("body,status", [
    ({}, 400),
    ({"concept_id": None, "concept": None}, 400),
    ({"concept_id": "tentafruit_island", "concept": {"title": "x"}}, 400),
    ({"concept_id": "nope"}, 404),
    ({"concept_id": "../tentafruit_island"}, 404),
    ({"concept_id": "gen_42"}, 404),
])
def test_choosing_needs_exactly_one_known_concept(api, body, status):
    story_id = _create(api)["story_id"]
    response = api.client.post(f"/api/stories/{story_id}/concepts/choose", json=body)
    assert response.status_code == status
    assert api.client.get(f"/api/stories/{story_id}").json()["story"]["status"] == "draft"


def test_the_bible_approval_survives_choosing_the_same_concept_again_only(api):
    story_id = _with_bible(api)

    story = api.client.post(f"/api/stories/{story_id}/concepts/choose",
                            json={"concept_id": "tentafruit_island"}).json()
    assert story["approvals"]["bible"] and story["status"] == "bible_approved"

    story = api.client.post(f"/api/stories/{story_id}/concepts/choose",
                            json={"concept_id": "midnight_fridge"}).json()
    assert story["approvals"]["bible"] is None and story["status"] == "concept_chosen"
    assert story["concept_id"] == "midnight_fridge"


def test_the_concept_cannot_change_while_the_bible_is_being_written(api):
    story_id = _chosen(api)
    job = api.client.post(f"/api/stories/{story_id}/steps/bible", json={}).json()

    response = api.client.post(f"/api/stories/{story_id}/concepts/choose", json={"concept_id": "midnight_fridge"})

    assert response.status_code == 409 and job["id"] in response.json()["detail"]
    assert api.client.get(f"/api/stories/{story_id}").json()["story"]["concept_id"] == "tentafruit_island"


# ==================================================================== style

def test_the_style_needs_an_approved_bible(api):
    story_id = _with_bible(api, approve=False)
    response = api.client.post(f"/api/stories/{story_id}/steps/style", json={})
    assert response.status_code == 409 and response.json()["detail"] == "Approve the bible first."
    assert not (api.outputs / "stories" / story_id / "style_lock.json").exists()


@pytest.mark.parametrize("params,needle", [
    ({"template_id": "vaporwave"}, "fruit_drama"),
    ({"template_id": "../fruit_drama"}, "shipped"),
    ({"colour": "red"}, "template_id"),
    ({"overrides": ["palette.accents"]}, "overrides"),
    ({"consistency_mode": "vibes"}, "prompt_only"),
])
def test_a_bad_style_parameter_is_a_400_and_writes_nothing(api, params, needle):
    story_id = _with_bible(api)
    response = api.client.post(f"/api/stories/{story_id}/steps/style", json={"params": params})
    assert response.status_code == 400
    assert needle in json.dumps(response.json()["detail"])
    assert not (api.outputs / "stories" / story_id / "style_lock.json").exists()


def test_refused_overrides_are_a_400_listing_every_error(api):
    story_id = _with_bible(api)
    response = api.client.post(f"/api/stories/{story_id}/steps/style", json={"params": {"overrides": {
        "palette.accents": ["yellow"], "camera": "handheld"}}})
    assert response.status_code == 400
    errors = response.json()["detail"]["errors"]
    assert any("palette.accents" in e for e in errors) and any("camera" in e for e in errors)
    assert not (api.outputs / "stories" / story_id / "style_lock.json").exists()


def test_a_draft_takes_more_overrides_and_a_new_template_starts_fresh(api):
    story_id = _with_bible(api)
    url = f"/api/stories/{story_id}/steps/style"

    api.client.post(url, json={"params": {"overrides": {"palette.accents": ["#FFD400"]}}})
    lock = api.client.post(url, json={"params": {
        "overrides": {"typography.ai_label": False}, "consistency_mode": "prompt_only"}}).json()
    assert lock["style_lock"]["overrides"] == {"palette.accents": ["#FFD400"], "typography.ai_label": False}
    assert lock["story"]["generation_profile"]["consistency_mode"] == "prompt_only"

    lock = api.client.post(url, json={"params": {"template_id": "claymation"}}).json()
    assert lock["style_lock"]["template_id"] == "claymation"
    assert lock["style_lock"]["overrides"] == {}
    assert lock["story"]["style_template_id"] == "claymation"


def test_the_style_approval_is_cleared_by_a_new_draft_and_locking_twice_is_a_409(api):
    story_id = _with_bible(api)
    c = api.client
    assert c.post(f"/api/stories/{story_id}/approve/style").status_code == 409  # nothing to approve

    c.post(f"/api/stories/{story_id}/steps/style", json={})
    assert c.post(f"/api/stories/{story_id}/approve/style").status_code == 200
    response = c.post(f"/api/stories/{story_id}/approve/style")
    assert response.status_code == 409 and "already locked" in response.json()["detail"]


def test_a_story_style_template_is_the_default_for_the_style_step(api):
    story_id = _create(api, style_template_id="anime")["story_id"]
    api.client.post(f"/api/stories/{story_id}/concepts/choose", json={"concept_id": "tentafruit_island"})
    api.client.patch(f"/api/stories/{story_id}", json=BIBLE_PATCH)
    api.client.post(f"/api/stories/{story_id}/approve/bible")

    body = api.client.post(f"/api/stories/{story_id}/steps/style", json={}).json()

    assert body["style_lock"]["template_id"] == "anime"


# ================================================================= get story

def test_the_story_page_carries_the_ledger_total(api):
    from clipping.aistory.ledger import CostLedger

    story_id = _create(api)["story_id"]
    ledger = CostLedger(str(api.outputs / "stories" / story_id / "cost_ledger.json"))
    ledger.append(step="style_preview", provider="fal", model="flux", unit="image", qty=1, est_usd=0.003, paid=True)
    ledger.append(step="style_preview", provider="pollinations", model="flux", unit="image", qty=2, est_usd=0.0, paid=False)

    assert api.client.get(f"/api/stories/{story_id}").json()["cost_total_usd"] == 0.003


# ================================================================= estimate

def test_the_estimate_of_each_phase_one_step(api):
    story_id = _create(api)["story_id"]
    url = f"/api/stories/{story_id}/estimate"
    link = {"link": "gemini/gemini-test", "keyed": True, "free": True}

    for step, calls in (("concepts", 5), ("bible", 3), ("regenerate", 1)):
        body = api.client.get(f"{url}/{step}").json()
        assert body == {
            "step": step, "est_usd": 0.0, "units": {"llm_calls": calls}, "route_class": "free",
            "link": "gemini/gemini-test", "links": [link], "ready": True, "message": body["message"],
        }
        assert "gemini/gemini-test" in body["message"]

    assert api.client.get(f"{url}/regenerate", params={"target": "concepts"}).json()["units"] == {"llm_calls": 5}
    assert api.client.get(f"{url}/regenerate", params={"target": "bible:tone"}).json()["units"] == {"llm_calls": 1}
    assert api.client.get(f"{url}/regenerate", params={"target": "shot:1:sh01"}).status_code == 400

    style = api.client.get(f"{url}/style").json()
    assert (style["units"], style["route_class"], style["est_usd"], style["ready"], style["link"]) == (
        {"llm_calls": 0}, "local", 0.0, True, None)

    # Stage 8: the preview has its own estimate (tests/test_style_preview.py).
    assert api.client.get(f"{url}/style_preview").json()["units"] == {"images": 3}
    assert api.client.get(f"{url}/cast").status_code == 400
    assert api.client.get(f"{url}/nope").status_code == 404


def test_an_estimate_with_no_key_is_blocked_with_the_gate_message(api):
    story_id = _create(api)["story_id"]
    _settings(api, {"LLM_CHAIN": "gemini/gemini-test"})

    body = api.client.get(f"/api/stories/{story_id}/estimate/bible").json()

    assert (body["route_class"], body["ready"], body["link"]) == ("blocked", False, None)
    assert body["links"] == [{"link": "gemini/gemini-test", "keyed": False, "free": True}]
    assert body["message"] == api.client.post(f"/api/stories/{story_id}/concepts/generate").json()["detail"]


def test_the_first_keyed_link_decides_free_or_paid(api):
    story_id = _create(api)["story_id"]
    url = f"/api/stories/{story_id}/estimate/concepts"

    _settings(api, {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model",
                    "OPENROUTER_API_KEY": "test-openrouter-key"})
    body = api.client.get(url).json()
    assert (body["route_class"], body["link"], body["ready"]) == ("paid", "openrouter/test-model", True)
    assert body["links"][0] == {"link": "gemini/gemini-test", "keyed": False, "free": True}

    _settings(api, {"LLM_CHAIN": "gemini/gemini-test,openrouter/test-model",
                    "GOOGLE_API_KEY": "test-gemini-key", "OPENROUTER_API_KEY": "test-openrouter-key"})
    body = api.client.get(url).json()
    assert (body["route_class"], body["link"], body["ready"]) == ("free", "gemini/gemini-test", True)
    assert "openrouter/test-model" in body["message"] and "billed" in body["message"]


def test_an_estimate_on_the_slow_floor_is_not_ready(api):
    story_id = _create(api)["story_id"]
    _settings(api, {"NVIDIA_API_KEY": "test-nvidia-key"})

    body = api.client.get(f"/api/stories/{story_id}/estimate/concepts").json()

    assert body["route_class"] == "free" and body["link"].startswith("nvidia/")
    assert body["ready"] is False and "GROQ_API_KEY" in body["message"]


def test_the_story_page_lists_its_jobs_without_their_feeds(api):
    # The story page is polled from a phone; a step job's feed can hold 500
    # events and is streamed separately (GET /api/jobs/{id}/status).
    story_id = _create(api)["story_id"]
    job = api.client.post(f"/api/stories/{story_id}/concepts/generate").json()

    listed = api.client.get(f"/api/stories/{story_id}").json()["jobs"]

    assert [j["id"] for j in listed] == [job["id"]]
    assert {"events", "log", "clips", "config", "progress"}.isdisjoint(listed[0])
    assert {"id", "step", "status", "created_at", "updated_at", "error", "params"} <= set(listed[0])


# ================================================== GET /api/stories/styles

def test_get_styles_lists_the_seven_shipped_templates(api):
    response = api.client.get("/api/stories/styles")
    assert response.status_code == 200
    body = response.json()
    ids = [s["template_id"] for s in body["styles"]]

    assert ids == sorted(templates.list_style_ids())
    assert len(ids) == 7
    for style in body["styles"]:
        assert set(style) == {"template_id", "version", "name", "palette", "typography", "episode_defaults"}
        assert set(style["name"]) == {"fr", "en"}
        assert isinstance(style["version"], int)
        assert set(style["palette"]) == {"primary", "accents", "forbidden", "palette_line"}
        assert set(style["typography"]) == {
            "font_family", "font_fallback", "subtitle_mode", "highlight_colour"}
        assert set(style["episode_defaults"]) == {"hook_style", "cliffhanger_style"}


def test_get_styles_matches_the_loaded_templates(api):
    body = api.client.get("/api/stories/styles").json()
    by_id = {s["template_id"]: s for s in body["styles"]}

    for template_id in templates.list_style_ids():
        template = templates.load_style(template_id)
        entry = by_id[template_id]
        assert entry["version"] == template["version"]
        assert entry["name"] == template["name"]
        assert entry["palette"] == template["palette"]
        assert entry["typography"]["font_family"] == template["typography"]["font_family"]
        assert entry["episode_defaults"]["hook_style"] == template["episode_defaults"]["hook_style"]


def test_the_styles_route_does_not_shadow_an_unknown_story_id(api):
    # /api/stories/styles is declared before /{story_id}; an unrelated
    # 12-hex id must still 404 rather than ever being confused with it.
    response = api.client.get(f"/api/stories/{UNKNOWN_ID}")
    assert response.status_code == 404


def test_the_styles_route_is_declared_before_the_story_id_route():
    text = APP_ROUTES.read_text(encoding="utf-8")
    assert '@router.get("/styles")' in text
    assert text.index('@router.get("/styles")') < text.index('@router.get("/{story_id}")')
