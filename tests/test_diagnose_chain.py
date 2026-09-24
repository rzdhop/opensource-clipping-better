"""Settings -> Test provider chain asks every keyed link the real question (DEC-078).

The diagnostic used to send "reply with ok". On 2026-09-24 it reported a chain
as ready whose only working link was the NVIDIA floor, because a ping cannot
tell "alive" from "can carry a job" (DEC-056 says so in as many words, and
DEC-058 is what it cost). ``llm.diagnose_chain`` sends each keyed link the
scan's own request on the fixture transcript, all providers at once, and says
what each one found.

No SDK, no network.
"""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from clipping.analysis import diagnostic
from clipping.providers import llm, pacing, registry
from clipping.providers.registry import Link, parse_chain

FOUND = json.dumps({"candidates": [
    {"b0": 4, "b1": 9, "score": 85, "gist": "ships on a Friday", "kind": "story"},
]})
EMPTY = json.dumps({"candidates": []})


def exc(name, status=None, message=None):
    cls = type(name, (Exception,), {})
    err = cls(message or f"simulated {name}")
    if status is not None:
        err.status_code = status
    return err


def is_work(kwargs):
    return kwargs.get("max_tokens", 0) > llm.PROBE_MAX_TOKENS


class Endpoint:
    """Fake providers. *answers* maps provider -> content, exception, or a
    callable ``(kwargs) -> content``; ``per_model`` overrides by model."""

    def __init__(self, answers=None, per_model=None, ping="ok"):
        self.answers = answers or {}
        self.per_model = per_model or {}
        self.ping = ping
        self.calls = []  # (provider, model, "work" | "ping")
        self._lock = threading.Lock()

    def factory(self, link, api_key=None, timeout=None):
        def create(**kwargs):
            kind = "work" if is_work(kwargs) else "ping"
            with self._lock:
                self.calls.append((link.provider, kwargs["model"], kind))
            answer = self.per_model.get(kwargs["model"],
                                        self.answers.get(link.provider, FOUND))
            if kind == "ping" and not isinstance(answer, Exception):
                answer = self.ping
            if callable(answer) and not isinstance(answer, Exception):
                answer = answer(kwargs)
            if isinstance(answer, Exception):
                raise answer
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=answer))],
                usage=SimpleNamespace(total_tokens=10, completion_tokens=5),
            )
        return SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)))


@pytest.fixture(autouse=True)
def _clean():
    llm.reset_negotiation()
    pacing.reset_limiters()
    yield
    llm.reset_negotiation()
    pacing.reset_limiters()


def diagnose(chain, keys, endpoint, **kwargs):
    return llm.diagnose_chain(
        parse_chain(chain), keys, diagnostic.diagnostic_work(),
        judge=diagnostic.judge, on_log=lambda *a: None,
        client_factory=endpoint.factory, **kwargs,
    )


# ------------------------------------------------------------ the question

def test_every_keyed_link_is_asked_the_real_analysis_request():
    seen = {}

    def record(provider):
        def answer(kwargs):
            seen[provider] = kwargs
            return FOUND
        return answer

    endpoint = Endpoint({p: record(p) for p in ("groq", "gemini", "nvidia")})
    probes = diagnose("groq/a,gemini/b,nvidia/c",
                      {"groq": "k", "gemini": "k", "nvidia": "k"}, endpoint)

    work = diagnostic.diagnostic_work()
    assert set(seen) == {"groq", "gemini", "nvidia"}
    for kwargs in seen.values():
        assert kwargs["messages"][-1]["content"] == work["user"]
        assert kwargs["max_tokens"] == work["max_tokens"]
    assert [p.kind for p in probes] == ["work"] * 3
    assert all(p.reason == "ok" for p in probes)


def test_a_keyless_link_is_skipped_without_a_request():
    endpoint = Endpoint()
    probes = diagnose("groq/a,gemini/b", {"gemini": "k"}, endpoint)
    assert (probes[0].kind, probes[0].reason) == ("skipped", "no API key")
    assert {p for p, _, _ in endpoint.calls} == {"gemini"}


def test_each_answer_is_judged_on_whether_it_found_the_clip():
    endpoint = Endpoint({"groq": FOUND, "gemini": EMPTY})
    found, empty = diagnose("groq/a,gemini/b", {"groq": "k", "gemini": "k"}, endpoint)

    assert found.judgement.found_moment and found.judgement.candidates == 1
    assert found.value == json.loads(FOUND)


def test_zero_candidates_is_live_but_flagged():
    """DEC-067: an empty answer is an opinion, not an outage. DEC-058: it is
    also what a useless model returns, so the row says so."""
    endpoint = Endpoint({"gemini": EMPTY})
    (probe,) = diagnose("gemini/b", {"gemini": "k"}, endpoint)

    assert (probe.kind, probe.reason) == ("work", "ok")
    assert probe.judgement.candidates == 0
    assert "found no moment" in probe.judgement.note


def test_the_row_names_the_model_that_answered(monkeypatch):
    monkeypatch.setitem(
        registry.PROVIDERS, "gemini",
        registry.PROVIDERS["gemini"]._replace(fallback_models=("fb-1",)),
    )
    endpoint = Endpoint(per_model={
        "old": exc("NotFoundError", 404, "model is no longer available to new users"),
    })
    (probe,) = diagnose("gemini/old", {"gemini": "k"}, endpoint)

    assert probe.label == "gemini/old"
    assert probe.used_model == "fb-1"
    assert probe.reason == "ok"


def test_the_row_names_the_structured_output_level_that_worked():
    endpoint = Endpoint()
    (probe,) = diagnose("gemini/b", {"gemini": "k"}, endpoint)
    assert probe.level == "json_schema"


# ------------------------------------------------ dead, slow, or unsuitable

def test_a_work_failure_falls_back_to_the_ping_and_reads_alive():
    """Reachable but unable to do the job: a different fix from a dead key."""
    endpoint = Endpoint({"gemini": "this is not json"})
    (probe,) = diagnose("gemini/b", {"gemini": "k"}, endpoint)

    assert (probe.kind, probe.reason) == ("ping", "ok")
    assert "JSON" in probe.work_error or "json" in probe.work_error
    kinds = [kind for _, _, kind in endpoint.calls]
    assert kinds[-1] == "ping" and "work" in kinds


def test_a_timed_out_work_request_is_not_followed_by_a_ping():
    """A link that used its whole allowance has no time left to ping in, and
    the route's budget is the sum of those allowances."""
    endpoint = Endpoint({"nvidia": exc("APITimeoutError", None, "Request timed out.")})
    (probe,) = diagnose("nvidia/c", {"nvidia": "k"}, endpoint)

    assert probe.kind == "work"
    assert "timed out" in probe.reason
    assert [kind for _, _, kind in endpoint.calls] == ["work"]


def test_a_dead_key_fails_both_questions():
    endpoint = Endpoint({"groq": exc("AuthenticationError", 401, "Invalid API Key")})
    (probe,) = diagnose("groq/a", {"groq": "k"}, endpoint)
    assert probe.kind == "ping"
    assert "AuthenticationError" in probe.reason
    assert "AuthenticationError" in probe.work_error


# ------------------------------------------------------------- scheduling

def test_links_on_different_providers_are_asked_at_the_same_time():
    """Each would wait for the other at the barrier; asked one after another,
    the first times out there and fails."""
    barrier = threading.Barrier(2, timeout=5)

    def meet(kwargs):
        barrier.wait()
        return FOUND

    endpoint = Endpoint({"groq": meet, "gemini": meet})
    probes = diagnose("groq/a,gemini/b", {"groq": "k", "gemini": "k"}, endpoint)

    assert [p.reason for p in probes] == ["ok", "ok"]
    assert [p.kind for p in probes] == ["work", "work"]


def test_links_on_one_provider_are_asked_one_at_a_time():
    """NVIDIA serialises requests per key and rate limits are per provider, so
    two links on one provider in parallel would only race each other."""
    state = {"now": 0, "peak": 0}
    lock = threading.Lock()

    def slow(kwargs):
        with lock:
            state["now"] += 1
            state["peak"] = max(state["peak"], state["now"])
        time.sleep(0.05)
        with lock:
            state["now"] -= 1
        return FOUND

    endpoint = Endpoint({"gemini": slow})
    diagnose("gemini/a,gemini/b,gemini/c", {"gemini": "k"}, endpoint)
    assert state["peak"] == 1


def test_results_come_back_in_chain_order():
    def slow(kwargs):
        time.sleep(0.1)
        return FOUND

    endpoint = Endpoint({"groq": slow, "gemini": FOUND})
    probes = diagnose("groq/a,gemini/b,groq/c", {"groq": "k", "gemini": "k"}, endpoint)
    assert [p.label for p in probes] == ["groq/a", "gemini/b", "groq/c"]


# ---------------------------------------------------------------- contract

def test_probe_chain_still_returns_4_tuples():
    """Guard: the preflight's contract is unchanged by the diagnostic."""
    endpoint = Endpoint()
    live, results, value = llm.probe_chain(
        parse_chain("groq/a"), {"groq": "k"},
        on_log=lambda *a: None, client_factory=endpoint.factory,
    )
    assert live == Link("groq", "a") and value is None
    assert [len(r) for r in results] == [4]


def test_a_diagnostic_record_starts_with_the_preflights_4_tuple():
    """So ``preflight_message`` can explain a diagnostic's results unchanged."""
    endpoint = Endpoint({"groq": exc("AuthenticationError", 401)})
    probes = diagnose("groq/a", {"groq": "k"}, endpoint)
    assert "groq/a" in llm.preflight_message(probes)


def test_the_providers_layer_does_not_import_the_analysis_layer():
    """RC-C8: the fixture and the judge are handed in, never imported."""
    source = (registry.__file__.replace("registry.py", "llm.py"))
    text = open(source, encoding="utf-8").read()
    assert "clipping.analysis" not in text and "from ..analysis" not in text


# ------------------------------------------------------------------ budget

def test_the_diagnostic_waits_as_long_as_a_job_request_would():
    """A timeout here must mean what it means in a job. The first design used
    the preflight's 90s work cap, and on 2026-09-24 Gemini's free tier took
    100.8s to answer a real window that a job, allowed 180s, got back fine."""
    for name in registry.PROVIDERS:
        if name == "custom":
            continue
        link = Link(name, "m")
        assert registry.diagnostic_timeout(link) == min(
            registry.effective_timeout(link), registry.DIAGNOSTIC_LINK_CAP_SECONDS)


def test_the_diagnostic_outwaits_the_slowest_healthy_answers_measured():
    """Measured 2026-09-24 on real requests that then succeeded."""
    assert registry.diagnostic_timeout(Link("gemini", "m")) > 100.8
    assert registry.diagnostic_timeout(Link("nvidia", "m")) > 193.0


def test_the_budget_covers_the_slowest_provider_and_fits_the_ceiling():
    """Providers run at once and links on one provider one after another, so
    the wait is the longest provider's sum, not the chain's."""
    links = parse_chain(registry.DEFAULT_LLM_CHAIN)
    keys = {link.provider: "k" for link in links}
    budget = registry.diagnostic_budget(links, keys)

    nvidia = registry.diagnostic_timeout(Link("nvidia", "m"))
    assert budget == nvidia + registry.DIAGNOSTIC_SLACK_SECONDS
    assert budget <= 300.0

    only_gemini = registry.diagnostic_budget(links, {"gemini": "k"})
    assert only_gemini == (registry.diagnostic_timeout(Link("gemini", "m"))
                           + registry.DIAGNOSTIC_SLACK_SECONDS)


def test_a_keyless_link_adds_nothing_to_the_budget():
    links = parse_chain("gemini/a,gemini/b")
    one = registry.diagnostic_budget(links[:1], {"gemini": "k"})
    two = registry.diagnostic_budget(links, {"gemini": "k"})
    none = registry.diagnostic_budget(links, {})
    assert two - registry.DIAGNOSTIC_SLACK_SECONDS == 2 * (one - registry.DIAGNOSTIC_SLACK_SECONDS)
    assert none == registry.DIAGNOSTIC_SLACK_SECONDS
