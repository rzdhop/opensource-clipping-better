"""The liveness check that runs before transcription.

A key proves a provider was configured, not that it is alive. The job this was
written for had one keyed link, passed the key gate, transcribed for 47 minutes
on CPU, and only then discovered that its provider answered nothing at all.

Stdlib only: no SDK, no network, no sleeping.
"""

from types import SimpleNamespace

import pytest

from clipping.providers import llm, pacing
from clipping.providers.registry import Link, parse_chain


class Clock:
    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now


def responder(clock, behaviour):
    """A client factory whose reply per provider comes from *behaviour*.

    Each entry is ``(seconds, outcome)``; *outcome* is an exception to raise or
    anything else to return as a reply.
    """
    seen = []

    class Completions:
        def __init__(self, provider):
            self.provider = provider

        def create(self, **kwargs):
            seen.append((self.provider, kwargs))
            seconds, outcome = behaviour[self.provider]
            clock.now += seconds
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

    def factory(link, **kwargs):
        return SimpleNamespace(
            chat=SimpleNamespace(completions=Completions(link.provider))
        )

    factory.seen = seen
    return factory


REPLY = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
    usage=SimpleNamespace(total_tokens=8),
)


@pytest.fixture(autouse=True)
def _clean():
    pacing.reset_limiters()
    yield
    pacing.reset_limiters()


# ------------------------------------------------------------- probe_chain

def test_the_first_link_that_answers_ends_the_probe():
    """A healthy run costs exactly one trivial request."""
    clock = Clock()
    factory = responder(clock, {"groq": (0.4, REPLY), "nvidia": (0.0, REPLY)})

    live, results = llm.probe_chain(
        parse_chain("groq/a,nvidia/b"), {"groq": "k", "nvidia": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live == Link("groq", "a")
    assert [provider for provider, _ in factory.seen] == ["groq"]
    assert results[0][1] == "ok"


def test_a_dead_first_link_does_not_stop_the_probe():
    """The point is whether ANYTHING answers, not whether the first one does."""
    clock = Clock()
    dead = TimeoutError("timed out")
    factory = responder(clock, {"groq": (20.0, dead), "nvidia": (1.0, REPLY)})

    live, _ = llm.probe_chain(
        parse_chain("groq/a,nvidia/b"), {"groq": "k", "nvidia": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live == Link("nvidia", "b")


def test_a_chain_where_nothing_answers_reports_every_link():
    clock = Clock()
    dead = TimeoutError("timed out")
    factory = responder(clock, {"groq": (20.0, dead), "nvidia": (20.0, dead)})

    live, results = llm.probe_chain(
        parse_chain("groq/a,nvidia/b"), {"groq": "k", "nvidia": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live is None
    message = llm.preflight_message(results)
    assert "groq/a" in message and "nvidia/b" in message
    assert "TimeoutError" in message
    assert "Nothing was transcribed" in message


def test_a_keyless_link_is_reported_but_never_contacted():
    clock = Clock()
    factory = responder(clock, {"nvidia": (0.5, REPLY)})

    live, results = llm.probe_chain(
        parse_chain("groq/a,nvidia/b"), {"nvidia": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live == Link("nvidia", "b")
    assert [provider for provider, _ in factory.seen] == ["nvidia"]
    assert results[0] == ("groq/a", "no API key", None)


def test_the_probe_is_small_and_unstructured():
    """No schema and no negotiation.

    Involving the structured-output ladder would let a provider's json_schema
    support decide a liveness question, and would cost several requests where
    one is the point.
    """
    clock = Clock()
    factory = responder(clock, {"groq": (0.2, REPLY)})

    llm.probe_chain(
        parse_chain("groq/a"), {"groq": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    _, kwargs = factory.seen[0]
    assert kwargs["max_tokens"] <= 8
    assert "response_format" not in kwargs
    assert len(kwargs["messages"]) == 1


def test_the_probe_timeout_is_far_below_the_request_timeout():
    """Measured, not chosen by taste.

    Healthy probes of the shipped default ran 1.3-11.4s; the dead model does not
    answer in 120s. The bound below keeps the probe well clear of the 330s
    request timeout it would otherwise inherit -- waiting that long would defeat
    the purpose -- while leaving room for a cold free tier.
    """
    assert 30 <= llm.PROBE_TIMEOUT_SECONDS <= 60
    captured = {}

    def factory(link, **kwargs):
        captured.update(kwargs)
        return SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **k: REPLY)))

    llm.probe_chain(parse_chain("nvidia/a"), {"nvidia": "k"},
                    on_log=lambda *a: None, client_factory=factory)
    assert captured["timeout"] == llm.PROBE_TIMEOUT_SECONDS


# ------------------------------------------------------- config.preflight_chain

class Cfg:
    ai_provider = "chain"
    llm_chain = "groq/a,nvidia/b"
    preflight = True
    api_key_groq = "k"
    api_key_gemini = ""
    api_key_nvidia = "k"
    api_key_openrouter = ""
    api_key_mistral = ""
    api_key_custom = ""


def preflight(cfg, behaviour, clock=None):
    from clipping.config import preflight_chain

    clock = clock or Clock()
    return preflight_chain(
        cfg, on_log=lambda *a: None,
        client_factory=responder(clock, behaviour), time_fn=clock,
    )


def test_a_live_chain_returns_no_complaint():
    assert preflight(Cfg(), {"groq": (0.3, REPLY)}) is None


def test_a_dead_chain_is_reported_rather_than_transcribed():
    dead = TimeoutError("timed out")
    message = preflight(Cfg(), {"groq": (20.0, dead), "nvidia": (20.0, dead)})
    assert message is not None
    assert "liveness check" in message
    assert "Nothing was transcribed" in message


def test_no_preflight_skips_the_check_entirely():
    class Off(Cfg):
        preflight = False

    dead = TimeoutError("timed out")
    assert preflight(Off(), {"groq": (20.0, dead), "nvidia": (20.0, dead)}) is None


def test_the_legacy_single_provider_paths_are_not_gated():
    """The escape hatch is not somewhere to add a new gate."""
    class Legacy(Cfg):
        ai_provider = "nvidia"

    dead = TimeoutError("timed out")
    assert preflight(Legacy(), {"groq": (20.0, dead), "nvidia": (20.0, dead)}) is None


def test_a_chain_with_no_keys_at_all_is_left_to_the_key_gate():
    """missing_provider_key owns that case and explains it better."""
    class NoKeys(Cfg):
        api_key_groq = ""
        api_key_nvidia = ""

    assert preflight(NoKeys(), {}) is None


def test_a_failing_link_is_never_removed_from_the_chain():
    """DEC-003 and DEC-023: the chain is a list the user wrote down.

    A probe reports; it does not edit.
    """
    cfg = Cfg()
    before = cfg.llm_chain
    preflight(cfg, {"groq": (20.0, TimeoutError("x")), "nvidia": (0.2, REPLY)})
    assert cfg.llm_chain == before
