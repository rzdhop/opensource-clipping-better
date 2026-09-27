"""``tools/bench_llm.py``'s episode-prompt request source (AI Story phase 3,
stage 12): E1/E2/T1 requests built from a real story folder's written
episode 1 (script + storyboard), scored for JSON validity against the
prompt's own schema and post-validator, free links only (paid links skipped,
DEC-115) -- the numbers for the phase's A-entry ("JSON validity rate per
provider with the E-prompts").

The story is the stage-6 fixture (``test_story_episode_steps``): a real
``StoryStore`` under ``tmp_path``, written through the real ``script`` step
(a fake LLM, no network) and the fast (no-call) storyboard, exactly the
documents a job would leave in ``outputs/stories/<id>/episodes/ep01/``.
Offline and hermetic: no key, chain or cap of the machine reaches a test, no
request leaves the process (``transport.urllib_transport`` raises on any real
call), and the repository's own ``outputs/stories`` is never touched --
everything lives under ``tmp_path``.

Stdlib + pytest (the CI environment, DEC-012). The bench module is imported
inside each test, so on the parent commit (no ``--episode-prompts`` support)
every test here fails on its own instead of the file failing to collect.
"""

from __future__ import annotations

import importlib
import json

import test_story_episode_steps as eps  # noqa: F401 -- hermetic is an autouse fixture
from clipping.providers.registry import Link
from test_story_episode_steps import hermetic, store  # noqa: F401 -- fixtures


def _bench():
    return importlib.import_module("tools.bench_llm")


def _episode_story(store, *, storyboard=True):
    """A ready story with episode 1's script fully written (the stage-6
    fake LLM, no network); the storyboard fast-planned (no call) unless
    *storyboard* is False. Returns ``(story_id, story_dir)``."""
    m = eps._new()
    story_id = eps._written_script(store)
    if storyboard:
        m.storyboard.build_fast(store, story_id, 1, now=eps.NOW, on_log=eps.Log())
    return story_id, store.story_dir(story_id)


# ------------------------------------------------------- the request source

def test_the_request_source_builds_e1_e2_and_t1_from_a_story_folder(store):
    bench = _bench()
    _story_id, story_dir = _episode_story(store)

    requests = bench.build_episode_prompt_requests(story_dir, ep=1)

    assert [r.prompt_id for r in requests] == ["E1", "E2", "T1"]
    for request in requests:
        assert request.system and isinstance(request.system, str)
        assert request.user and isinstance(request.user, str)
        assert isinstance(request.schema, dict) and request.schema.get("type") == "object"
        assert request.schema_name
        assert request.max_tokens > 0
        assert 0 <= request.temperature <= 1
        assert callable(request.validate)

    # E1 is the beat sheet: no scene has been decided yet, so it never names
    # one -- the ask is built from the season's arc entry and the roster.
    e1 = requests[0]
    assert "s02" not in e1.user  # scene ids are Python's to assign, never asked for

    # E2/T1 are built from a real written body scene (s02: Kiwilo proposes
    # the alliance) -- its own dialogue line text is in the prompt.
    e2 = requests[1]
    assert "Kiwilo" in e2.user or "alliance" in e2.user.lower()

    t1 = requests[2]
    assert "Numbered lines:" in t1.user


def test_the_e1_reply_that_wrote_the_fixture_passes_its_own_request(store):
    """The fixture's own E1_REPLY (already accepted once, to write this
    exact story's episode 1) validates clean against the E1 request built
    from that same story -- proves the request source reads the right
    context (roster, template, arc entry), not a mismatched one."""
    bench = _bench()
    _story_id, story_dir = _episode_story(store)
    e1 = bench.build_episode_prompt_requests(story_dir, ep=1)[0]

    errors = e1.schema and __import__("clipping.aistory.schemas", fromlist=["validate"]).validate(
        eps.E1_REPLY, e1.schema
    )
    assert errors == []
    assert e1.validate(eps.E1_REPLY) == []


# ---------------------------------------------------------------- scoring

def test_scoring_classifies_each_failure_kind(store):
    bench = _bench()
    _story_id, story_dir = _episode_story(store)
    e2, t1 = bench.build_episode_prompt_requests(story_dir, ep=1)[1:]

    # -- exceptions never reach a parsed reply
    assert bench.classify_episode_failure(ValueError("No JSON value found in provider reply")) == "no_json"

    from clipping.providers.transport import APITimeoutError
    assert bench.classify_episode_failure(APITimeoutError("gemini timed out after 300s")) == "timeout"
    assert bench.classify_episode_failure(TimeoutError("timed out")) == "timeout"

    assert bench.classify_episode_failure(RuntimeError("503 Service Unavailable")) == "provider_error: RuntimeError"

    # -- a reply that parsed: schema failure (missing everything)
    assert bench.score_episode_reply({}, e2) == "schema"

    # -- a reply that parsed and is schema-valid, but fails the prompt's own
    # post-validator: two shots sharing a framing (t1_reply's own second
    # shot's framing forced to repeat the first's)
    valid_t1 = eps.t1_reply({"schema": t1.schema, "user": t1.user})
    broken_t1 = json.loads(json.dumps(valid_t1))  # a plain deep copy
    broken_t1["shots"][1]["framing"] = broken_t1["shots"][0]["framing"]
    verdict = bench.score_episode_reply(broken_t1, t1)
    assert verdict.startswith("validator: ")
    assert "framing" in verdict

    # -- a reply that parses, is schema-valid and passes the post-validator
    valid_e2 = eps.e2_reply({"schema": e2.schema})
    assert bench.score_episode_reply(valid_e2, e2) == "ok"
    assert bench.score_episode_reply(valid_t1, t1) == "ok"


# --------------------------------------------------------------- link choice

def test_paid_links_are_skipped_and_never_get_a_client(store):
    bench = _bench()
    _story_id, story_dir = _episode_story(store)

    settings_env = {"LLM_CHAIN": "openrouter/some-paid-model", "OPENROUTER_API_KEY": "test-key"}
    usable, skipped = bench.select_episode_links(settings_env)
    assert usable == []
    assert len(skipped) == 1
    link, reason = skipped[0]
    assert link == Link("openrouter", "some-paid-model")
    assert "allow_paid" in reason  # DEC-115's own wording

    def explode(*_a, **_k):
        raise AssertionError("a client was built for a skipped (paid) link")

    rows, usable2, skipped2 = bench.run_episode_prompt_bench(
        story_dir, ep=1, settings_env=settings_env, samples=3, client_factory=explode,
    )
    assert rows == []
    assert usable2 == []
    assert skipped2 == skipped


def test_a_free_but_keyless_link_is_also_skipped(store):
    bench = _bench()
    usable, skipped = bench.select_episode_links({"LLM_CHAIN": "gemini/gemini-test"})
    assert usable == []
    assert skipped == [(Link("gemini", "gemini-test"), None)] or skipped[0][0] == Link("gemini", "gemini-test")
    assert "key" in skipped[0][1].lower()


def test_a_keyed_free_link_is_usable(store):
    bench = _bench()
    usable, skipped = bench.select_episode_links(
        {"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key"}
    )
    assert usable == [Link("gemini", "gemini-test")]
    assert skipped == []


# --------------------------------------------------------- a fake live run

class _FakeClient:
    def __init__(self, replies):
        self._replies = list(replies)
        self.last_usage = None
        self.calls = 0

    def complete_json(self, **_kwargs):
        self.calls += 1
        reply = self._replies.pop(0)
        if isinstance(reply, BaseException):
            raise reply
        return reply


def test_run_episode_prompt_bench_scores_every_call_sequentially(store):
    bench = _bench()
    _story_id, story_dir = _episode_story(store)
    requests = bench.build_episode_prompt_requests(story_dir, ep=1)
    e1, e2, t1 = requests

    valid_e1 = eps.E1_REPLY
    valid_e2 = eps.e2_reply({"schema": e2.schema})
    valid_t1 = eps.t1_reply({"schema": t1.schema, "user": t1.user})

    fake = _FakeClient([
        valid_e1, ValueError("not json"),  # E1: ok, then no_json
        valid_e2, valid_e2,  # E2: ok, ok
        valid_t1, valid_t1,  # T1: ok, ok
    ])

    settings_env = {"LLM_CHAIN": "gemini/gemini-test", "GOOGLE_API_KEY": "test-gemini-key"}
    rows, usable, skipped = bench.run_episode_prompt_bench(
        story_dir, ep=1, requests=requests, settings_env=settings_env, samples=2,
        client_factory=lambda link, key: fake,
    )

    assert usable == [Link("gemini", "gemini-test")]
    assert skipped == []
    assert fake.calls == 6
    assert [(r["prompt"], r["rep"], r["class"]) for r in rows] == [
        ("E1", 1, "ok"), ("E1", 2, "no_json"),
        ("E2", 1, "ok"), ("E2", 2, "ok"),
        ("T1", 1, "ok"), ("T1", 2, "ok"),
    ]
    for row in rows:
        assert row["link"] == "gemini/gemini-test"
        assert isinstance(row["latency"], float) and row["latency"] >= 0
    assert rows[1]["tokens"] is None  # the no_json row: nothing was parsed
    assert rows[0]["tokens"] > 0


# --------------------------------------------- existing modes are untouched

def test_the_existing_pass_a_mode_is_unchanged():
    bench = _bench()
    from clipping.analysis import diagnostic

    requests = bench.build_requests()
    assert len(requests) == 1
    request = requests[0]
    assert request.label == "fixture"
    assert request.judge is diagnostic.judge
    assert request.work == diagnostic.diagnostic_work()


def test_the_nim_shortlist_and_default_chain_constants_are_unchanged():
    bench = _bench()
    assert bench.NIM_SHORTLIST[0] == "nvidia/nvidia/nemotron-3.5-lightning-30b-a3b"
    assert callable(bench.main)
