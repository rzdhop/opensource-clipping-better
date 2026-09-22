"""The LLM client: structured-output negotiation, retries, and the chain.

A fake client replays scripted responses and exceptions, exactly as
``tests/test_nvidia_retry.py`` does for the legacy path. No SDK, no network, no
sleeping.
"""

import json
from types import SimpleNamespace

import pytest

from clipping.providers import errors, llm, pacing, registry
from clipping.providers.registry import Link, parse_chain


# ------------------------------------------------------------------- fakes

class FakeCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        if not self._owner.scripted:
            raise AssertionError("fake client ran out of scripted responses")
        item = self._owner.scripted.pop(0)
        if isinstance(item, Exception):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))],
            usage=SimpleNamespace(total_tokens=self._owner.usage_tokens),
        )


class FakeClient:
    def __init__(self, scripted, usage_tokens=100):
        self.scripted = list(scripted)
        self.calls = []
        self.usage_tokens = usage_tokens
        self.chat = SimpleNamespace(completions=FakeCompletions(self))


def exc(name, status=None, message=None, headers=None):
    """An exception whose class name matches an OpenAI SDK error."""
    cls = type(name, (Exception,), {})
    err = cls(message or f"simulated {name}")
    if status is not None:
        err.status_code = status
    if headers is not None:
        err.response = SimpleNamespace(status_code=status, headers=headers)
    return err


SCHEMA = {"type": "array", "items": {"type": "object"}}
GOOD = json.dumps([{"a": 1}])


@pytest.fixture(autouse=True)
def _clean():
    llm.reset_negotiation()
    pacing.reset_limiters()
    yield
    llm.reset_negotiation()
    pacing.reset_limiters()


@pytest.fixture
def no_pacing():
    """A limiter that never waits, so pacing is not under test here."""
    return pacing.Limiter("test", rpm=None, tpm=None)


def make_client(link, scripted, no_pacing, **kwargs):
    fake = FakeClient(scripted)
    client = llm.LlmClient(
        link,
        api_key="k",
        client_factory=lambda *a, **kw: fake,
        limiter=no_pacing,
        on_log=lambda *a: None,
        **kwargs,
    )
    return client, fake


# ------------------------------------------------------------ the SDK policy

def test_the_sdks_own_retries_are_disabled(monkeypatch):
    """DEC-019. The SDK defaults to max_retries=2 and retries anything >= 500,
    which once turned a reported 'attempt 3/3' into nine silent requests."""
    captured = {}

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    module = SimpleNamespace(OpenAI=FakeOpenAI)
    monkeypatch.setitem(__import__("sys").modules, "openai", module)

    llm.build_client(Link("groq", "m"), api_key="k", timeout=42)

    assert captured["max_retries"] == 0
    assert captured["timeout"] == 42
    assert captured["base_url"] == "https://api.groq.com/openai/v1"


def test_nvidia_gets_an_explicit_timeout_above_the_gateway_cutoff(no_pacing):
    client, _ = make_client(
        Link("nvidia", "deepseek-ai/deepseek-v4-flash-0731"), [GOOD], no_pacing
    )
    assert client.timeout == 330


# ------------------------------------------------------------- negotiation

def test_a_capable_provider_uses_json_schema(no_pacing):
    client, fake = make_client(Link("groq", "m"), [GOOD], no_pacing)
    client.complete_json(system="s", user="u", schema=SCHEMA)

    assert fake.calls[0]["response_format"]["type"] == "json_schema"
    assert fake.calls[0]["response_format"]["json_schema"]["strict"] is True


def test_a_rejection_falls_back_to_json_object_then_prompt(no_pacing):
    """DEC-013, generalized. The first real analysis this project ever ran died
    on `400 unknown field 'guided_json'`."""
    rejection = exc("BadRequestError", 400, "unknown field 'response_format'")
    client, fake = make_client(
        Link("groq", "m"), [rejection, rejection, GOOD], no_pacing
    )

    client.complete_json(system="s", user="u", schema=SCHEMA)

    levels = [call.get("response_format", {}).get("type") for call in fake.calls]
    assert levels == ["json_schema", "json_object", None]


def test_a_rejection_is_not_a_retry(no_pacing):
    """Walking down the ladder must not consume the retry budget — the request
    was never really attempted at that level."""
    rejection = exc("BadRequestError", 400, "response_format is not supported")
    chain = [Link("groq", "m")]
    fake = FakeClient([rejection, rejection, GOOD])
    value, _ = llm.run_chain(
        chain,
        system="s",
        user="u",
        schema=SCHEMA,
        keys={"groq": "k"},
        on_log=lambda *a: None,
        client_factory=lambda *a, **kw: fake,
        sleep_fn=lambda s: None,
    )
    assert value == [{"a": 1}]
    assert len(fake.calls) == 3


def test_the_negotiated_level_is_remembered_for_the_next_request(no_pacing):
    rejection = exc("BadRequestError", 400, "unknown field 'response_format'")
    client, fake = make_client(
        Link("groq", "m"), [rejection, GOOD, GOOD], no_pacing
    )

    client.complete_json(system="s", user="u", schema=SCHEMA)
    client.complete_json(system="s", user="u", schema=SCHEMA)

    levels = [call.get("response_format", {}).get("type") for call in fake.calls]
    # Second request starts where the first one succeeded, not back at the top.
    assert levels == ["json_schema", "json_object", "json_object"]
    assert llm.negotiated_level(Link("groq", "m")) == "json_object"


def test_a_level_is_only_remembered_once_it_produced_parseable_json(no_pacing):
    """A provider that accepts json_schema and then ignores it is worse than one
    that refuses it, so remembering must happen after the parse, not before."""
    client, _ = make_client(Link("groq", "m"), ["not json at all"], no_pacing)
    with pytest.raises(ValueError):
        client.complete_json(system="s", user="u", schema=SCHEMA)
    assert llm.negotiated_level(Link("groq", "m")) is None


def test_a_provider_declaring_no_schema_support_starts_lower(no_pacing):
    client, fake = make_client(Link("mistral", "m"), [GOOD], no_pacing)
    client.complete_json(system="s", user="u", schema=SCHEMA)
    assert fake.calls[0]["response_format"]["type"] == "json_object"


def test_no_schema_means_no_response_format(no_pacing):
    client, fake = make_client(Link("groq", "m"), [GOOD], no_pacing)
    client.complete_json(system="s", user="u", schema=None)
    assert "response_format" not in fake.calls[0]


def test_deepseek_on_nvidia_disables_thinking(no_pacing):
    """A reasoning preamble is the difference between answering and hitting the
    gateway's ~300s cut-off on a provider measured at ~12-13 tokens/s."""
    client, fake = make_client(
        Link("nvidia", "deepseek-ai/deepseek-v4-flash-0731"), [GOOD], no_pacing
    )
    client.complete_json(system="s", user="u", schema=SCHEMA)
    assert fake.calls[0]["extra_body"] == {"chat_template_kwargs": {"thinking": False}}


def test_other_models_send_no_extra_body(no_pacing):
    client, fake = make_client(Link("groq", "openai/gpt-oss-120b"), [GOOD], no_pacing)
    client.complete_json(system="s", user="u", schema=SCHEMA)
    assert "extra_body" not in fake.calls[0]


# ------------------------------------------------------------------ retries

def _run(scripted, chain="groq/m", keys=None, **kwargs):
    fake = FakeClient(scripted)
    logs = []
    value, link = llm.run_chain(
        parse_chain(chain),
        system="s",
        user="u",
        schema=SCHEMA,
        keys=keys if keys is not None else {"groq": "k", "nvidia": "k", "gemini": "k"},
        on_log=logs.append,
        client_factory=lambda *a, **kw: fake,
        sleep_fn=lambda s: None,
        **kwargs,
    )
    return value, link, fake, logs


def test_a_transient_failure_is_retried(no_pacing):
    value, _, fake, _ = _run([exc("InternalServerError", 503), GOOD])
    assert value == [{"a": 1}]
    assert len(fake.calls) == 2


def test_malformed_json_is_retried(no_pacing):
    value, _, fake, _ = _run(["this is not json", GOOD])
    assert value == [{"a": 1}]
    assert len(fake.calls) == 2


def test_the_ladder_stops_after_max_attempts(no_pacing):
    fake = FakeClient([exc("InternalServerError", 503)] * 5)
    with pytest.raises(errors.ProviderError):
        llm.run_chain(
            parse_chain("groq/m"),
            system="s", user="u", schema=SCHEMA,
            keys={"groq": "k"},
            on_log=lambda *a: None,
            client_factory=lambda *a, **kw: fake,
            sleep_fn=lambda s: None,
        )
    assert len(fake.calls) == llm.MAX_ATTEMPTS


def test_temperature_cools_on_each_retry(no_pacing):
    _, _, fake, _ = _run([exc("InternalServerError", 503), "bad json", GOOD])
    temps = [call["temperature"] for call in fake.calls]
    assert temps[0] > temps[1] > temps[2]


def test_a_fatal_error_is_not_retried(no_pacing):
    """A bad key stays bad; burning three attempts on it wastes the budget."""
    fake = FakeClient([exc("AuthenticationError", 401)] * 3)
    with pytest.raises(errors.ProviderError):
        llm.run_chain(
            parse_chain("groq/m"),
            system="s", user="u", schema=SCHEMA,
            keys={"groq": "k"},
            on_log=lambda *a: None,
            client_factory=lambda *a, **kw: fake,
            sleep_fn=lambda s: None,
        )
    assert len(fake.calls) == 1


def test_the_attempt_counter_uses_the_wording_the_dashboard_matches():
    """web/api/signals.py greps `attempt N/M` to drive the retry counter
    (DEC-014, the stdout-progress entry). Reword this and the counter silently
    stops appearing while the line is still shown verbatim."""
    from web.api import signals

    _, _, _, logs = _run([exc("InternalServerError", 503), GOOD])
    counters = [signals.attempt_from(line) for line in logs]
    assert (1, llm.MAX_ATTEMPTS) in counters


# --------------------------------------------------------------- rate limits

def test_a_429_waits_the_time_the_provider_asked_for(no_pacing):
    slept = []
    fake = FakeClient([exc("RateLimitError", 429, headers={"retry-after": "7"}), GOOD])
    llm.run_chain(
        parse_chain("groq/m"),
        system="s", user="u", schema=SCHEMA,
        keys={"groq": "k"},
        on_log=lambda *a: None,
        client_factory=lambda *a, **kw: fake,
        sleep_fn=slept.append,
    )
    assert slept == [7.0]


def test_a_429_wait_does_not_consume_a_retry(no_pacing):
    """Otherwise two rate limits would exhaust a three-attempt ladder without a
    single real failure."""
    scripted = [
        exc("RateLimitError", 429, headers={"retry-after": "1"}),
        exc("RateLimitError", 429, headers={"retry-after": "1"}),
        exc("InternalServerError", 503),
        exc("InternalServerError", 503),
        GOOD,
    ]
    value, _, fake, _ = _run(scripted)
    assert value == [{"a": 1}]
    assert len(fake.calls) == 5


def test_endless_rate_limiting_eventually_gives_up(no_pacing):
    fake = FakeClient([exc("RateLimitError", 429, headers={"retry-after": "1"})] * 9)
    with pytest.raises(errors.ProviderError):
        llm.run_chain(
            parse_chain("groq/m"),
            system="s", user="u", schema=SCHEMA,
            keys={"groq": "k"},
            on_log=lambda *a: None,
            client_factory=lambda *a, **kw: fake,
            sleep_fn=lambda s: None,
        )
    assert len(fake.calls) <= llm.MAX_RATE_LIMIT_WAITS + 1


# --------------------------------------------------------------- the chain

def test_the_chain_advances_only_after_a_link_is_exhausted(no_pacing):
    clients = []

    def factory(link, **kwargs):
        fake = FakeClient(
            [exc("InternalServerError", 503)] * 3 if link.provider == "groq" else [GOOD]
        )
        fake.link = link
        clients.append(fake)
        return fake

    value, link = llm.run_chain(
        parse_chain("groq/a,nvidia/b"),
        system="s", user="u", schema=SCHEMA,
        keys={"groq": "k", "nvidia": "k"},
        on_log=lambda *a: None,
        client_factory=factory,
        sleep_fn=lambda s: None,
    )

    assert value == [{"a": 1}]
    assert link.provider == "nvidia"
    assert len(clients[0].calls) == llm.MAX_ATTEMPTS   # groq fully exhausted
    assert len(clients[1].calls) == 1


def test_a_provider_not_in_the_chain_is_never_contacted(no_pacing):
    """The invariant that makes this an explicit chain and not the silent
    cross-provider fallback DEC-003 forbade."""
    contacted = []

    def factory(link, **kwargs):
        contacted.append(link.provider)
        return FakeClient([GOOD])

    llm.run_chain(
        parse_chain("nvidia/b"),
        system="s", user="u", schema=SCHEMA,
        keys={"groq": "k", "nvidia": "k", "gemini": "k", "openrouter": "k"},
        on_log=lambda *a: None,
        client_factory=factory,
        sleep_fn=lambda s: None,
    )
    assert contacted == ["nvidia"]


def test_a_link_with_no_key_is_skipped_not_fatal(no_pacing):
    """A partially-configured chain degrades to the providers actually set up."""
    contacted = []

    def factory(link, **kwargs):
        contacted.append(link.provider)
        return FakeClient([GOOD])

    value, link = llm.run_chain(
        parse_chain("groq/a,nvidia/b"),
        system="s", user="u", schema=SCHEMA,
        keys={"nvidia": "k"},          # no groq key
        on_log=lambda *a: None,
        client_factory=factory,
        sleep_fn=lambda s: None,
    )
    assert contacted == ["nvidia"]
    assert link.provider == "nvidia"


def test_every_link_failing_raises_with_all_the_reasons(no_pacing):
    def factory(link, **kwargs):
        return FakeClient([exc("AuthenticationError", 401)])

    with pytest.raises(errors.ProviderError) as info:
        llm.run_chain(
            parse_chain("groq/a,nvidia/b"),
            system="s", user="u", schema=SCHEMA,
            keys={"groq": "k", "nvidia": "k"},
            on_log=lambda *a: None,
            client_factory=factory,
            sleep_fn=lambda s: None,
        )
    assert len(info.value.failures) == 2
    assert "groq/a" in str(info.value) and "nvidia/b" in str(info.value)


def test_every_hop_is_printed(no_pacing):
    def factory(link, **kwargs):
        return FakeClient([exc("AuthenticationError", 401)] if link.provider == "groq"
                          else [GOOD])

    _, _, _, = None, None, None
    logs = []
    llm.run_chain(
        parse_chain("groq/a,nvidia/b"),
        system="s", user="u", schema=SCHEMA,
        keys={"groq": "k", "nvidia": "k"},
        on_log=logs.append,
        client_factory=factory,
        sleep_fn=lambda s: None,
    )
    joined = "\n".join(logs)
    assert "groq/a" in joined and "nvidia/b" in joined


def test_a_spent_time_budget_skips_the_remaining_links(no_pacing):
    """DEC-020's predictive check, carried into the chain."""
    now = [0.0]

    def factory(link, **kwargs):
        return FakeClient([exc("InternalServerError", 503)] * 3)

    with pytest.raises(errors.ProviderError) as info:
        llm.run_chain(
            parse_chain("groq/a,nvidia/b"),
            system="s", user="u", schema=SCHEMA,
            keys={"groq": "k", "nvidia": "k"},
            on_log=lambda *a: None,
            client_factory=factory,
            sleep_fn=lambda s: now.__setitem__(0, now[0] + 1000),
            deadline=100.0,
            time_fn=lambda: now[0],
        )
    reasons = " ".join(reason for _, reason in info.value.failures)
    assert "budget" in reasons


# ------------------------------------------------------------------- usage

def test_reported_usage_is_fed_back_to_the_limiter(no_pacing):
    client, _ = make_client(Link("groq", "m"), [GOOD], no_pacing)
    client.complete_json(system="s", user="u", schema=SCHEMA)
    assert no_pacing.snapshot()[1] == 100


def test_a_reply_with_no_choices_is_a_retryable_value_error(no_pacing):
    class Empty:
        choices = []
        usage = None

    client = llm.LlmClient(
        Link("groq", "m"),
        api_key="k",
        client_factory=lambda *a, **kw: SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=lambda **kw2: Empty())
            )
        ),
        limiter=no_pacing,
        on_log=lambda *a: None,
    )
    with pytest.raises(ValueError):
        client.complete_json(system="s", user="u", schema=SCHEMA)


# ------------------------------------------- the budget check is predictive

class Clock:
    """A fake monotonic clock a scripted request can push forward."""

    def __init__(self, start=0.0):
        self.now = float(start)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += float(seconds)


def slow_factory(clock, seconds, outcome):
    """A client whose every request costs *seconds* of clock and then *outcome*."""

    class SlowCompletions:
        def __init__(self, owner):
            self._owner = owner

        def create(self, **kwargs):
            self._owner.calls.append(kwargs)
            clock.advance(seconds)
            if isinstance(outcome, Exception):
                raise outcome
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=outcome))],
                usage=SimpleNamespace(total_tokens=100),
            )

    class Slow:
        def __init__(self):
            self.calls = []
            self.chat = SimpleNamespace(completions=SlowCompletions(self))

    made = []

    def factory(link, **kwargs):
        client = Slow()
        made.append(client)
        return client

    return factory, made


def test_an_attempt_that_cannot_finish_inside_the_budget_is_not_started(no_pacing):
    """The recorded job, as a unit test.

    NVIDIA's request timeout is 330s and the analysis budget is 900s. Three
    attempts at ~302s each ran to ~925s and overran the budget, because the only
    guard weighed the 4s/12s backoff instead of the request. The third attempt
    must not start: 608 + 12 + 330 = 950 > 900.
    """
    clock = Clock()
    factory, made = slow_factory(clock, 302, exc("InternalServerError", 504))
    logs = []

    with pytest.raises(Exception):
        llm.run_chain(
            parse_chain("nvidia/deepseek-ai/deepseek-v4.1-flash"),
            system="s", user="u", schema=SCHEMA,
            keys={"nvidia": "k"},
            on_log=logs.append,
            client_factory=factory,
            sleep_fn=clock.advance,
            deadline=900.0,
            time_fn=clock,
        )

    assert len(made[0].calls) == 2, "a third 330s attempt cannot fit in 900s"
    assert clock.now < 900.0, "the ladder must not overrun the budget it is given"
    assert "budget" in "\n".join(logs)


def test_the_attempt_counter_never_reports_a_refused_attempt(no_pacing):
    """web/api/signals.py turns `attempt N/M` into a retry the dashboard shows.

    An attempt that was refused for want of time was never made, so printing it
    would report a retry that never happened -- the exact class of misreporting
    DEC-019 was written about.
    """
    clock = Clock()
    factory, _ = slow_factory(clock, 302, exc("InternalServerError", 504))
    logs = []

    with pytest.raises(Exception):
        llm.run_chain(
            parse_chain("nvidia/m"),
            system="s", user="u", schema=SCHEMA,
            keys={"nvidia": "k"},
            on_log=logs.append,
            client_factory=factory,
            sleep_fn=clock.advance,
            deadline=900.0,
            time_fn=clock,
        )

    joined = "\n".join(logs)
    assert "attempt 1/3" in joined
    assert "attempt 2/3" in joined
    assert "attempt 3/3" not in joined


def test_a_link_that_cannot_fit_is_skipped_but_a_faster_one_is_still_tried(no_pacing):
    """Per link, not a blanket abort.

    With 200s left, NVIDIA's 330s request cannot fit but Groq's 120s one can.
    Aborting the whole chain there would throw away a provider that was ready to
    answer.
    """
    clock = Clock(start=700.0)
    logs = []
    answered = {}

    def factory(link, **kwargs):
        answered["link"] = link
        return FakeClient([GOOD])

    value, link = llm.run_chain(
        parse_chain("nvidia/slow,groq/fast"),
        system="s", user="u", schema=SCHEMA,
        keys={"nvidia": "k", "groq": "k"},
        on_log=logs.append,
        client_factory=factory,
        sleep_fn=lambda s: None,
        deadline=900.0,
        time_fn=clock,
    )

    assert link.provider == "groq"
    assert answered["link"].provider == "groq", "the slow link was never contacted"
    assert "Skipping nvidia/slow" in "\n".join(logs)


def test_the_budget_check_uses_the_clients_own_timeout(no_pacing):
    """The invariant whose absence made the old check decoration.

    If the number the budget reasons about and the number the socket waits for
    can drift apart, the check proves nothing.
    """
    for name in registry.PROVIDER_NAMES:
        if name == "custom":
            continue  # needs LLM_CUSTOM_BASE_URL; covered in its own test
        link = Link(name, "m")
        client = llm.LlmClient(
            link, api_key="k", client_factory=lambda *a, **kw: FakeClient([]),
            limiter=no_pacing, on_log=lambda *a: None,
        )
        assert client.timeout == registry.effective_timeout(link), name


def test_a_fast_provider_still_gets_all_three_attempts(no_pacing):
    """The budget is a ceiling, not a shortcut.

    DEC-020 sized it to be at least two full-length requests precisely so a
    slow-but-healthy call is never cut off; cutting one off would be a
    regression dressed as a fix.
    """
    clock = Clock()
    factory, made = slow_factory(clock, 2, exc("InternalServerError", 503))

    with pytest.raises(Exception):
        llm.run_chain(
            parse_chain("groq/fast"),
            system="s", user="u", schema=SCHEMA,
            keys={"groq": "k"},
            on_log=lambda *a: None,
            client_factory=factory,
            sleep_fn=clock.advance,
            deadline=900.0,
            time_fn=clock,
        )

    assert len(made[0].calls) == 3


def test_no_deadline_means_no_budget_check(no_pacing):
    """A caller that passes no deadline keeps the old, unbounded behaviour."""
    clock = Clock()
    factory, made = slow_factory(clock, 5000, exc("InternalServerError", 503))

    with pytest.raises(Exception):
        llm.run_chain(
            parse_chain("nvidia/m"),
            system="s", user="u", schema=SCHEMA,
            keys={"nvidia": "k"},
            on_log=lambda *a: None,
            client_factory=factory,
            sleep_fn=clock.advance,
            time_fn=clock,
        )

    assert len(made[0].calls) == 3


def test_the_real_provider_error_survives_a_budget_stop(no_pacing):
    """Stopping early must not replace the diagnosis with a stopwatch reading.

    A 504 says the gateway gave up; "out of budget" says only that we stopped
    asking. The caller needs the first one.
    """
    clock = Clock()
    factory, _ = slow_factory(clock, 302, exc("InternalServerError", 504))

    with pytest.raises(errors.ProviderError) as info:
        llm.run_chain(
            parse_chain("nvidia/m"),
            system="s", user="u", schema=SCHEMA,
            keys={"nvidia": "k"},
            on_log=lambda *a: None,
            client_factory=factory,
            sleep_fn=clock.advance,
            deadline=900.0,
            time_fn=clock,
        )

    reasons = " ".join(reason for _, reason in info.value.failures)
    assert "InternalServerError" in reasons
