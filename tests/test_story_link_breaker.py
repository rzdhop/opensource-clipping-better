"""Plan 28 stage A5: a provider link that died during a job is not asked
again for the rest of that job, and the AI Story chain tries the reliable free
link first (DEC-305).

Every call goes through the real ``llm.run_chain`` with ``llm.build_client``
replaced by a fake provider that answers from a queue: no network, test keys.
The breaker's memory is ``StepContext.link_health``, one dict per job.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import functools
import json
from types import SimpleNamespace

import pytest

from clipping.aistory import steps
from clipping.aistory.steps import llm_call
from clipping.cancel import CancelToken
from clipping.providers import budget, llm, pacing, registry

SYSTEM = "You write the story bible."
USER = "Écris la bible de l'histoire. " * 20
VALID = json.dumps({"ok": True})
ULTRA = "nvidia/nvidia/nemotron-3-ultra-550b-a55b"
FLOOR = "gemini/gemini-test"
KEYS = {"NVIDIA_API_KEY": "test-nvidia-key", "GOOGLE_API_KEY": "test-gemini-key"}


def _http_error(status, name="InternalServerError"):
    exc = type(name, (Exception,), {})(f"Error code: {status} - upstream")
    exc.status_code = status
    return exc


class Fake:
    """``llm.build_client``'s stand-in; each provider has a queue of answers
    (a content string or an exception to raise), every request is recorded."""

    def __init__(self):
        self.queues = {}
        self.sent = []

    def answer(self, provider, *answers):
        self.queues.setdefault(provider, []).extend(answers)

    def __call__(self, link, *, api_key, timeout):
        create = functools.partial(self._create, link)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def _create(self, link, **body):
        self.sent.append(registry.describe(link))
        queue = self.queues.get(link.provider) or []
        if not queue:
            raise AssertionError(f"{registry.describe(link)} was sent a request nobody queued")
        answer = queue.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=answer), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15),
        )


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in ("LLM_CHAIN", "STORY_LLM_CHAIN", "STORY_LLM_PREMIUM_CHAIN", "ALLOW_SLOW_CHAIN", *budget.ENV_NAMES):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "spend.json"))
    monkeypatch.setattr(llm, "BACKOFF_SECONDS", (0, 0))
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    yield
    budget.reset()
    pacing.reset_limiters()
    llm.reset_negotiation()
    llm.reset_model_fallbacks()


@pytest.fixture
def world(tmp_path, monkeypatch):
    fake = Fake()
    monkeypatch.setattr(llm, "build_client", fake)
    spec = f"{ULTRA},{FLOOR}"
    settings = {"LLM_CHAIN": spec, "STORY_LLM_PREMIUM_CHAIN": spec, **KEYS}

    def ctx(health=None):
        log = []
        return steps.StepContext(
            job_id="job000000001", story_id="s1", step="bible", ep=None, params={}, cancel=CancelToken(),
            settings_env=dict(settings), outputs_dir=str(tmp_path), on_log=log.append,
            **({} if health is None else {"link_health": health}),
        ), log

    return SimpleNamespace(fake=fake, ctx=ctx)


def _call(ctx, validator=lambda value: []):
    return llm_call.call_json(ctx, "B1", SYSTEM, USER, {}, validator=validator, time_fn=lambda: 1000.0)


def _ultra_sent(fake):
    return [label for label in fake.sent if label == ULTRA]


# ------------------------------------------------------------------ the rule

def test_a_link_that_failed_its_ladder_on_5xx_is_skipped_for_the_rest_of_the_job(world):
    fake = world.fake
    ctx, log = world.ctx()
    fake.answer("nvidia", *[_http_error(503)] * 3)
    fake.answer("gemini", VALID, VALID)

    assert _call(ctx) == {"ok": True}
    assert len(_ultra_sent(fake)) == 3  # the whole ladder, once
    assert ULTRA in ctx.link_health
    skipped = [line for line in log if "skipped for the rest of this job" in line]
    assert len(skipped) == 1
    assert "nemotron-3-ultra-550b-a55b" in skipped[0] and "failed 3 times" in skipped[0]

    # The second call of the same job never contacts the dead link.
    ctx2 = steps.StepContext(**{**ctx.__dict__})  # the fast track's sub-context shares the dict
    assert ctx2.link_health is ctx.link_health
    assert _call(ctx2) == {"ok": True}
    assert len(_ultra_sent(fake)) == 3
    assert fake.sent[-1] == FLOOR


def test_the_memory_is_per_job_a_new_job_asks_the_link_again(world):
    fake = world.fake
    ctx, _log = world.ctx()
    fake.answer("nvidia", *[_http_error(500)] * 3, VALID)
    fake.answer("gemini", VALID)
    _call(ctx)
    assert ULTRA in ctx.link_health

    other_job, _ = world.ctx()
    assert other_job.link_health == {}
    assert _call(other_job) == {"ok": True}
    assert len(_ultra_sent(fake)) == 4  # three failures, then the new job's one answer
    assert ULTRA not in other_job.link_health


def test_the_default_context_has_its_own_empty_memory(world):
    a, _ = world.ctx()
    b, _ = world.ctx()
    assert a.link_health == {} and a.link_health is not b.link_health


@pytest.mark.parametrize("exc", [
    _http_error(0, "APITimeoutError"),
    _http_error(0, "APIConnectionError"),
])
def test_a_timeout_or_a_dropped_connection_opens_it_too(world, exc):
    ctx, _log = world.ctx()
    world.fake.answer("nvidia", exc, exc, exc)
    world.fake.answer("gemini", VALID)
    _call(ctx)
    assert ULTRA in ctx.link_health


def test_a_validation_rejection_does_not_open_it(world):
    fake = world.fake
    ctx, _log = world.ctx()
    fake.answer("nvidia", VALID, VALID)  # it answers; the validator refuses twice
    fake.answer("gemini", VALID)
    refusals = iter([["too long"], ["too long"], []])

    assert _call(ctx, validator=lambda value: next(refusals)) == {"ok": True}
    assert ctx.link_health == {}


def test_a_429_wait_does_not_open_it(world, monkeypatch):
    ctx, _log = world.ctx()
    monkeypatch.setattr(llm, "MAX_RATE_LIMIT_SLEEP", 0.0)
    limited = _http_error(429, "RateLimitError")
    world.fake.answer("nvidia", limited, limited, limited, limited, limited)
    world.fake.answer("gemini", VALID)
    _call(ctx)
    assert ctx.link_health == {}


def test_a_4xx_does_not_open_it(world):
    ctx, _log = world.ctx()
    world.fake.answer("nvidia", _http_error(400, "BadRequestError"))
    world.fake.answer("gemini", VALID)
    _call(ctx)
    assert ctx.link_health == {}


def test_when_every_link_is_open_the_chain_is_tried_as_it_is(world):
    fake = world.fake
    health = {ULTRA: {"times": 3}, FLOOR: {"times": 3}}
    ctx, _log = world.ctx(health)
    fake.answer("nvidia", VALID)
    assert _call(ctx) == {"ok": True}
    assert fake.sent == [ULTRA]


@pytest.mark.parametrize("reason, outage", [
    ("InternalServerError: Error code: 503 - overloaded", True),
    ("APIStatusError: Error code: 500", True),
    ("HTTPStatusError: HTTP 502 from https://x", True),
    ("APITimeoutError: Request timed out.", True),
    ("APIConnectionError: Connection error.", True),
    ("RateLimitError: Error code: 429", False),
    ("BadRequestError: Error code: 400", False),
    ("ValueError: No JSON value found", False),
    ("no API key (NVIDIA_API_KEY is not set)", False),
    ("a 330s request does not fit the 100s left in the time budget", False),
])
def test_what_counts_as_an_outage(reason, outage):
    assert llm_call.is_outage_reason(reason) is outage


# ------------------------------------------------------------- the chain order

def test_the_default_chain_order_is_pinned_by_dec_305():
    assert [registry.describe(link) for link in registry.parse_chain(registry.DEFAULT_STORY_LLM_CHAIN)] == [
        "gemini/gemini-3.5-flash-lite",
        "openrouter/mistralai/mistral-medium-3.1",
        "nvidia/nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nvidia/nemotron-3-super-120b-a12b",
    ]


def test_with_allow_paid_off_the_paid_link_is_left_out_and_gemini_still_leads():
    usable, skipped = llm_call.story_chain({})
    assert [registry.describe(link) for link in usable] == [
        "gemini/gemini-3.5-flash-lite",
        "nvidia/nvidia/nemotron-3-ultra-550b-a55b",
        "nvidia/nvidia/nemotron-3-super-120b-a12b",
    ]
    assert [registry.describe(link) for link, _reason in skipped] == ["openrouter/mistralai/mistral-medium-3.1"]


# ------------------------------------------------------- the finish_reason line

def _client(on_log):
    link = registry.parse_spec(FLOOR)
    fake = Fake()
    fake.answer("gemini", VALID)
    return llm.LlmClient(link, api_key="k", client_factory=lambda *a, **kw: fake(link, api_key="k", timeout=1),
                         on_log=on_log), fake


def test_a_reply_logs_its_finish_reason_and_completion_tokens():
    lines = []
    client, _fake = _client(lines.append)
    assert client.complete_json(system="s", user="u") == {"ok": True}
    assert lines == ["   ℹ️ gemini/gemini-test reply ended: finish_reason=stop, completion_tokens=5"]


def test_a_reply_without_those_fields_logs_question_marks():
    lines = []
    link = registry.parse_spec(FLOOR)
    reply = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=VALID))])
    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **body: reply)))
    client = llm.LlmClient(link, api_key="k", client_factory=lambda *a, **kw: fake, on_log=lines.append)
    client.complete_json(system="s", user="u")
    assert lines == ["   ℹ️ gemini/gemini-test reply ended: finish_reason=?, completion_tokens=?"]
