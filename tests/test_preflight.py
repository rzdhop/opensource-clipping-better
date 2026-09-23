"""The liveness check that runs before transcription.

A key proves a provider was configured, not that it is alive. The job this was
written for had one keyed link, passed the key gate, transcribed for 47 minutes
on CPU, and only then discovered that its provider answered nothing at all.

Stdlib only: no SDK, no network, no sleeping.
"""

from types import SimpleNamespace

import pytest

from clipping.providers import llm, pacing, registry
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

    live, results, _value = llm.probe_chain(
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

    live, _results, _value = llm.probe_chain(
        parse_chain("groq/a,nvidia/b"), {"groq": "k", "nvidia": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live == Link("nvidia", "b")


def test_a_chain_where_nothing_answers_reports_every_link():
    clock = Clock()
    dead = TimeoutError("timed out")
    factory = responder(clock, {"groq": (20.0, dead), "nvidia": (20.0, dead)})

    live, results, _value = llm.probe_chain(
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

    live, results, _value = llm.probe_chain(
        parse_chain("groq/a,nvidia/b"), {"nvidia": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live == Link("nvidia", "b")
    assert [provider for provider, _ in factory.seen] == ["nvidia"]
    # The fourth field records which probe ran; a keyless link runs neither.
    assert results[0] == ("groq/a", "no API key", None, "skipped")


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
    """A probe must never be able to wait as long as a real request.

    It used to also assert ONE number for every provider (45s, DEC-056). That
    half was the bug: on 2026-09-23 the NIM free tier took ~50s to answer this
    exact ping with a working key, and an nvidia link built with 45s called a
    live provider dead. The invariant worth keeping is the ceiling (DEC-072).
    """
    captured = {}

    def factory(link, **kwargs):
        captured[link.provider] = kwargs["timeout"]
        return SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **k: REPLY)))

    for spec, key in (("groq/a", "groq"), ("nvidia/a", "nvidia")):
        llm.probe_chain(parse_chain(spec), {key: "k"},
                        on_log=lambda *a: None, client_factory=factory)
        link = parse_chain(spec)[0]
        assert captured[key] == registry.probe_timeout(link)
        assert captured[key] < registry.effective_timeout(link)

    assert captured["groq"] == llm.PROBE_TIMEOUT_SECONDS
    assert captured["nvidia"] > captured["groq"]


def timed_responder(clock, behaviour):
    """Like ``responder``, but the client HONOURS the timeout it was built with.

    ``responder`` advances the clock by the scripted seconds whatever timeout
    the client carries, so no test built on it can tell 45s from 120s. Here a
    scripted duration longer than the client's timeout raises a timeout after
    exactly that timeout -- which is what the SDK does on the wire.
    """
    built = {}

    def factory(link, **kwargs):
        timeout = kwargs["timeout"]
        built[link.provider] = timeout

        def create(**_):
            seconds, outcome = behaviour[link.provider]
            if seconds > timeout:
                clock.now += timeout
                raise TimeoutError("Request timed out.")
            clock.now += seconds
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        return SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)))

    factory.built = built
    return factory


def test_a_queued_but_healthy_nim_link_is_live():
    """The reported job, as a unit test: only NVIDIA keyed, NVIDIA answering
    "ok" after 50s of queue. It used to be reported dead after 45s."""
    clock = Clock()
    factory = timed_responder(clock, {"nvidia": (50.0, REPLY)})

    live, results, _ = llm.probe_chain(
        parse_chain("groq/a,gemini/b,nvidia/c"), {"nvidia": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live is not None and live.provider == "nvidia"
    assert results[-1][1] == "ok"


def test_a_fast_tier_still_gets_the_short_probe():
    """Per provider, not a global bump to 120s: a hosted fast tier that takes
    50s to answer an 8-token question is still called dead at 45s."""
    clock = Clock()
    factory = timed_responder(clock, {"groq": (50.0, REPLY)})

    live, results, _ = llm.probe_chain(
        parse_chain("groq/a"), {"groq": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live is None
    assert "TimeoutError" in results[0][1]
    assert results[0][2] == pytest.approx(registry.DEFAULT_PROBE_TIMEOUT)


def test_an_explicit_timeout_still_overrides_every_link():
    """The seam the other tests in this file rely on."""
    clock = Clock()
    factory = timed_responder(clock, {"nvidia": (50.0, REPLY)})

    live, _, _ = llm.probe_chain(
        parse_chain("nvidia/c"), {"nvidia": "k"}, timeout=10,
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live is None
    assert factory.built["nvidia"] == 10


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


def test_a_non_chain_provider_is_not_gated():
    """Only the chain path is checked. Nothing else reaches the analyzer, but
    the guard stays: a provider value this function does not own is left alone
    rather than probed on a chain it never asked for."""
    class Other(Cfg):
        ai_provider = "openai_compat"

    dead = TimeoutError("timed out")
    assert preflight(Other(), {"groq": (20.0, dead), "nvidia": (20.0, dead)}) is None


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


# ---------------------------------------- a probe that proves it can do the work

CANDIDATES_REPLY = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(
        content='{"candidates": [{"b0": 0, "b1": 3, "score": 80, '
                '"gist": "a thing happens", "kind": "story"}]}'
    ))],
    usage=SimpleNamespace(total_tokens=120),
)

EMPTY_REPLY = SimpleNamespace(
    choices=[SimpleNamespace(message=SimpleNamespace(content='{"candidates": []}'))],
    usage=SimpleNamespace(total_tokens=9),
)


def _vtt(path, n_sentences=60):
    """A transcript on disk, so the probe has something real to ask about."""
    lines = ["WEBVTT", ""]
    t = 0.0
    for i in range(n_sentences):
        start, end = t, t + 2.5
        lines += [
            f"{_ts(start)} --> {_ts(end)}",
            f"Sentence number {i} says something interesting here.",
            "",
        ]
        t = end + 0.3
    path.write_text("\n".join(lines), encoding="utf-8")
    return str(path)


def _ts(seconds):
    m, s = divmod(float(seconds), 60)
    h, m = divmod(int(m), 60)
    return f"{h:02d}:{m:02d}:{s:06.3f}"


class WorkCfg(Cfg):
    """A chain job that already has its transcript, so no Whisper is pending."""

    platform = "tiktok"
    topic = ""
    output_language = "en"
    analysis_cache = True


def test_a_real_probe_is_used_when_a_transcript_is_already_on_hand(tmp_path):
    cfg = WorkCfg()
    cfg.transcript_path = _vtt(tmp_path / "t.vtt")
    cfg.outputs_dir = str(tmp_path)

    clock = Clock()
    factory = responder(clock, {"groq": (2.0, CANDIDATES_REPLY)})
    from clipping.config import preflight_chain

    assert preflight_chain(
        cfg, on_log=lambda *a: None, client_factory=factory, time_fn=clock
    ) is None

    _provider, kwargs = factory.seen[0]
    assert kwargs["max_tokens"] > llm.PROBE_MAX_TOKENS
    assert "BEATS:" in kwargs["messages"][-1]["content"]


def test_without_a_transcript_the_cheap_ping_is_still_used(tmp_path):
    """The 47-minutes-of-Whisper guarantee is what preflight is FOR. On that
    path there is no transcript yet, so there is nothing real to ask."""
    cfg = WorkCfg()
    cfg.transcript_path = None
    cfg.outputs_dir = str(tmp_path)

    clock = Clock()
    factory = responder(clock, {"groq": (0.3, REPLY)})
    from clipping.config import preflight_chain

    assert preflight_chain(
        cfg, on_log=lambda *a: None, client_factory=factory, time_fn=clock
    ) is None

    _provider, kwargs = factory.seen[0]
    assert kwargs["max_tokens"] == llm.PROBE_MAX_TOKENS


def test_a_probe_returning_no_candidates_is_still_live(tmp_path):
    """Zero candidates from one window is an opinion about 45 beats, not
    evidence the provider is dead."""
    cfg = WorkCfg()
    cfg.transcript_path = _vtt(tmp_path / "t.vtt")
    cfg.outputs_dir = str(tmp_path)

    clock = Clock()
    from clipping.config import preflight_chain

    assert preflight_chain(
        cfg, on_log=lambda *a: None,
        client_factory=responder(clock, {"groq": (1.0, EMPTY_REPLY)}),
        time_fn=clock,
    ) is None


def test_a_failed_work_probe_falls_back_to_the_ping(tmp_path):
    """DEC-020: a slow-but-healthy provider is never cut off. A link that
    cannot do the work in 90s but answers a ping is alive, and the chain is a
    list the user wrote down -- reporting it dead would be wrong."""
    cfg = WorkCfg()
    cfg.transcript_path = _vtt(tmp_path / "t.vtt")
    cfg.outputs_dir = str(tmp_path)

    clock = Clock()
    calls = {"n": 0}

    class Completions:
        def __init__(self, provider):
            self.provider = provider

        def create(self, **kwargs):
            calls["n"] += 1
            if kwargs["max_tokens"] > llm.PROBE_MAX_TOKENS:
                clock.now += 90.0
                raise TimeoutError("the work probe timed out")
            clock.now += 0.4
            return REPLY

    def factory(link, **kwargs):
        return SimpleNamespace(
            chat=SimpleNamespace(completions=Completions(link.provider))
        )

    from clipping.config import preflight_chain

    assert preflight_chain(
        cfg, on_log=lambda *a: None, client_factory=factory, time_fn=clock
    ) is None
    assert calls["n"] >= 2, "the ping was never tried after the work probe failed"


def test_a_dead_link_is_still_reported_not_removed(tmp_path):
    """DEC-003/023: the chain is a list the user wrote down."""
    cfg = WorkCfg()
    cfg.transcript_path = _vtt(tmp_path / "t.vtt")
    cfg.outputs_dir = str(tmp_path)

    dead = TimeoutError("timed out")
    clock = Clock()
    from clipping.config import preflight_chain

    message = preflight_chain(
        cfg, on_log=lambda *a: None,
        client_factory=responder(clock, {"groq": (20.0, dead),
                                         "nvidia": (20.0, dead)}),
        time_fn=clock,
    )
    assert message is not None
    assert parse_chain(cfg.llm_chain) == [Link("groq", "a"), Link("nvidia", "b")]


def test_a_successful_work_probe_seeds_the_scan_cache(tmp_path):
    """The whole reason to do real work here: the answer is kept, so pass A
    hits it instead of asking the same question again minutes later."""
    from clipping.analysis import cache as cache_mod
    from clipping.config import preflight_chain

    cfg = WorkCfg()
    cfg.transcript_path = _vtt(tmp_path / "t.vtt")
    cfg.outputs_dir = str(tmp_path)

    clock = Clock()
    preflight_chain(
        cfg, on_log=lambda *a: None,
        client_factory=responder(clock, {"groq": (2.0, CANDIDATES_REPLY)}),
        time_fn=clock,
    )

    stored = (tmp_path / cache_mod.CACHE_FILENAME)
    assert stored.is_file(), "the probe's answer was thrown away"


def test_the_work_probe_is_bounded(tmp_path):
    """It runs before transcription, so it must not become the delay it exists
    to prevent -- even against a link whose own timeout is 330s."""
    cfg = WorkCfg()
    cfg.llm_chain = "nvidia/b"
    cfg.transcript_path = _vtt(tmp_path / "t.vtt")
    cfg.outputs_dir = str(tmp_path)

    clock = Clock()
    factory = responder(clock, {"nvidia": (1.0, CANDIDATES_REPLY)})
    from clipping.config import preflight_chain

    captured = {}

    def capturing(link, **kwargs):
        captured[link.provider] = kwargs["timeout"]
        return factory(link, **kwargs)

    preflight_chain(
        cfg, on_log=lambda *a: None, client_factory=capturing, time_fn=clock
    )
    link = parse_chain("nvidia/b")[0]
    # Twice the ping allowance, and never the 330s a real request may take.
    assert captured["nvidia"] == registry.work_probe_timeout(link)
    assert captured["nvidia"] < registry.effective_timeout(link)


def test_the_work_probe_cap_is_unchanged_for_the_fast_tiers(tmp_path):
    """90s = 2 x 45s before DEC-072, and still, for every default-probe tier."""
    for name in ("groq", "gemini", "openrouter", "mistral"):
        assert registry.work_probe_timeout(Link(name, "m")) == 90.0


# ------------------------------------------------ every link, for a diagnostic

def test_by_default_the_probe_still_stops_at_the_first_live_link():
    clock = Clock()
    factory = responder(clock, {"groq": (1.0, REPLY), "nvidia": (1.0, REPLY)})

    live, results, _ = llm.probe_chain(
        parse_chain("groq/a,nvidia/b"), {"groq": "k", "nvidia": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
    )

    assert live.provider == "groq"
    assert [p for p, _ in factory.seen] == ["groq"]
    assert len(results) == 1


def test_a_diagnostic_pings_every_link_and_reports_the_first_live_one():
    """"Groq answered" says nothing about the NVIDIA link that broke."""
    clock = Clock()
    factory = responder(clock, {
        "groq": (1.0, REPLY),
        "nvidia": (2.0, TimeoutError("Request timed out.")),
    })

    live, results, _ = llm.probe_chain(
        parse_chain("groq/a,gemini/b,nvidia/c"), {"groq": "k", "nvidia": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
        stop_at_first=False,
    )

    assert live.provider == "groq"
    assert [r[1] for r in results] == ["ok", "no API key", results[2][1]]
    assert "TimeoutError" in results[2][1]
    assert [p for p, _ in factory.seen] == ["groq", "nvidia"]


def test_a_diagnostic_where_nothing_answers_is_still_none():
    clock = Clock()
    factory = responder(clock, {"groq": (1.0, TimeoutError("x"))})
    live, results, _ = llm.probe_chain(
        parse_chain("groq/a"), {"groq": "k"},
        on_log=lambda *a: None, client_factory=factory, time_fn=clock,
        stop_at_first=False,
    )
    assert live is None and len(results) == 1
