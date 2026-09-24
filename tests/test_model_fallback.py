"""A retired model costs one printed hop on the same key, not the link (DEC-077).

On 2026-09-24 Google closed ``gemini-2.5-flash-lite`` to new accounts. The key
was fine, the provider was fine, and the whole Gemini link failed anyway, so a
new user's job fell through to the NVIDIA floor. A provider that names
``fallback_models`` in the registry now answers that one error by trying the
next of its own models on the same key. Nothing else changes: another provider
is never contacted, every swap is printed, and a model that fails for any other
reason fails the link exactly as before.

No SDK, no network, no sleeping.
"""

import json
from types import SimpleNamespace

import pytest

from clipping.providers import errors, llm, pacing, registry
from clipping.providers.registry import Link, parse_chain

GOOD = json.dumps({"ok": 1})
SCHEMA = {"type": "object"}


def exc(name, status=None, message=None):
    cls = type(name, (Exception,), {})
    err = cls(message or f"simulated {name}")
    if status is not None:
        err.status_code = status
    return err


def gone(model="old"):
    """What Google actually answered on 2026-09-24, give or take the model."""
    return exc(
        "NotFoundError", 404,
        f"Error code: 404 - [{{'error': {{'code': 404, 'message': 'This model "
        f"models/{model} is no longer available to new users. Please update "
        f"your code to use models/gemini-3.5-flash-lite', 'status': 'NOT_FOUND'}}}}]",
    )


class Endpoint:
    """One fake provider account: answers per model, records every call."""

    def __init__(self, answers):
        self.answers = {model: list(items) for model, items in answers.items()}
        self.calls = []  # (provider, model, key)

    def factory(self, link, api_key=None, timeout=None):
        def create(**kwargs):
            model = kwargs["model"]
            self.calls.append((link.provider, model, api_key))
            queue = self.answers.get(model)
            if not queue:
                raise AssertionError(f"no scripted answer left for {model}")
            item = queue.pop(0) if len(queue) > 1 else queue[0]
            if isinstance(item, Exception):
                raise item
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=item))],
                usage=SimpleNamespace(total_tokens=10),
            )
        return SimpleNamespace(chat=SimpleNamespace(
            completions=SimpleNamespace(create=create)))

    def models(self, provider=None):
        return [m for p, m, _ in self.calls if provider in (None, p)]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    pacing.reset_limiters()
    # A known fallback list, independent of whatever the registry ships.
    monkeypatch.setitem(
        registry.PROVIDERS, "gemini",
        registry.PROVIDERS["gemini"]._replace(fallback_models=("fb-1", "fb-2")),
    )
    yield
    llm.reset_negotiation()
    llm.reset_model_fallbacks()
    pacing.reset_limiters()


def run(chain, endpoint, *, keys=None, logs=None, deadline=None, time_fn=None):
    kwargs = {}
    if deadline is not None:
        kwargs.update(deadline=deadline, time_fn=time_fn)
    return llm.run_chain(
        parse_chain(chain),
        system="s", user="u", schema=SCHEMA,
        keys=keys or {"gemini": "key-a"},
        on_log=(logs.append if logs is not None else (lambda *a: None)),
        client_factory=endpoint.factory,
        sleep_fn=lambda s: None,
        **kwargs,
    )


# ------------------------------------------------------------ the one case

def test_a_retired_model_is_replaced_by_the_providers_fallback_on_the_same_key():
    endpoint = Endpoint({"old": [gone()], "fb-1": [GOOD]})
    logs = []

    value, link = run("gemini/old", endpoint, logs=logs)

    assert value == {"ok": 1}
    assert link == Link("gemini", "fb-1")
    assert endpoint.models() == ["old", "fb-1"]
    assert {key for _, _, key in endpoint.calls} == {"key-a"}
    swap = [line for line in logs if "↪" in line]
    assert len(swap) == 1
    assert "gemini/old" in swap[0] and "fb-1" in swap[0]
    assert "same gemini key" in swap[0]


def test_a_model_fallback_never_leaves_the_provider():
    """DEC-023: a provider absent from the chain is never contacted, and a
    model swap is not a way around that."""
    endpoint = Endpoint({"old": [gone()], "fb-1": [gone("fb-1")], "fb-2": [gone("fb-2")]})

    with pytest.raises(errors.ProviderError):
        run("gemini/old", endpoint, keys={"gemini": "k", "openrouter": "k", "groq": "k"})

    assert {p for p, _, _ in endpoint.calls} == {"gemini"}


def test_every_model_failing_reports_every_reason():
    endpoint = Endpoint({"old": [gone()], "fb-1": [gone("fb-1")], "fb-2": [gone("fb-2")]})

    with pytest.raises(errors.ProviderError) as caught:
        run("gemini/old", endpoint)

    text = str(caught.value)
    for model in ("old", "fb-1", "fb-2"):
        assert f"{model}:" in text or f"/{model}" in text, model
    assert "no longer available" in text
    assert endpoint.models() == ["old", "fb-1", "fb-2"]


def test_the_next_link_still_answers_when_every_model_is_gone():
    endpoint = Endpoint({
        "old": [gone()], "fb-1": [gone("fb-1")], "fb-2": [gone("fb-2")],
        "m": [GOOD],
    })
    value, link = run("gemini/old,groq/m", endpoint, keys={"gemini": "k", "groq": "k"})
    assert link == Link("groq", "m")


# --------------------------------------------------------- what stays fixed

def test_a_swap_does_not_add_retries():
    """A dead model fails on its first attempt, so the fallback gets the link's
    full ladder and nothing more: 1 (the 404) + MAX_ATTEMPTS."""
    endpoint = Endpoint({"old": [gone()], "fb-1": [exc("InternalServerError", 503)]})

    with pytest.raises(errors.ProviderError):
        run("gemini/old", endpoint)

    assert endpoint.models() == ["old"] + ["fb-1"] * llm.MAX_ATTEMPTS


def test_a_non_model_failure_on_the_fallback_is_not_swapped_again():
    endpoint = Endpoint({"old": [gone()], "fb-1": [exc("AuthenticationError", 401)],
                         "fb-2": [GOOD]})

    with pytest.raises(errors.ProviderError) as caught:
        run("gemini/old", endpoint)

    assert endpoint.models() == ["old", "fb-1"]
    assert "AuthenticationError" in str(caught.value)
    assert "no longer available" in str(caught.value)


@pytest.mark.parametrize("failure", [
    exc("AuthenticationError", 401, "Incorrect API key provided"),
    exc("PermissionDeniedError", 403, "model not found in your project"),
    exc("RateLimitError", 429, "Rate limit reached for model"),
    exc("InternalServerError", 503, "This model is currently experiencing high demand"),
    exc("BadRequestError", 400, "temperature must be between 0 and 2"),
])
def test_an_auth_rate_limit_or_request_error_never_swaps_the_model(failure):
    endpoint = Endpoint({"old": [failure], "fb-1": [GOOD]})

    with pytest.raises(errors.ProviderError):
        run("gemini/old", endpoint)

    assert "fb-1" not in endpoint.models()


def test_a_provider_without_fallbacks_still_fails_fast_on_404():
    """Guard. The NIM floor names no fallbacks, so its 404 costs one call and
    moves the chain on, exactly as before this change."""
    endpoint = Endpoint({"m": [exc("NotFoundError", 404, "Function abc: Not found for account")]})

    with pytest.raises(errors.ProviderError):
        run("nvidia/m", endpoint, keys={"nvidia": "k"})

    assert endpoint.models() == ["m"]


def test_a_fallback_obeys_the_deadline():
    """The swap's first attempt passes the same predictive check as any other:
    no request starts that could outlast the budget, and the reason keeps the
    404 that caused the swap."""
    now = [0.0]

    def clock():
        return now[0]

    class Slow404(Endpoint):
        def factory(self, link, api_key=None, timeout=None):
            client = super().factory(link, api_key, timeout)
            real = client.chat.completions.create

            def create(**kwargs):
                now[0] += 30.0  # the 404 took 30s: the budget is now too small
                return real(**kwargs)
            client.chat.completions.create = create
            return client

    endpoint = Slow404({"old": [gone()], "fb-1": [GOOD]})
    timeout = registry.effective_timeout(Link("gemini", "old"))

    with pytest.raises(errors.ProviderError) as caught:
        run("gemini/old", endpoint, deadline=timeout + 10.0, time_fn=clock)

    assert endpoint.models() == ["old"]
    assert "no longer available" in str(caught.value)


# --------------------------------------------------------------- memory

def test_the_swap_is_remembered_per_key():
    """"No longer available to new users" is a fact about an account, so the
    next request on the same key goes straight to the model that worked, and
    another key finds out for itself."""
    endpoint = Endpoint({"old": [gone()], "fb-1": [GOOD]})
    run("gemini/old", endpoint, keys={"gemini": "key-a"})
    first = len(endpoint.calls)

    run("gemini/old", endpoint, keys={"gemini": "key-a"})
    assert endpoint.models()[first:] == ["fb-1"]

    second = len(endpoint.calls)
    run("gemini/old", endpoint, keys={"gemini": "key-b"})
    assert endpoint.models()[second:] == ["old", "fb-1"]


def test_the_memory_holds_no_key_material():
    endpoint = Endpoint({"old": [gone()], "fb-1": [GOOD]})
    run("gemini/old", endpoint, keys={"gemini": "sk-very-secret-value"})
    assert "sk-very-secret-value" not in repr(llm._MODEL_SWAPS)


def test_a_remembered_swap_that_dies_too_walks_the_list_again():
    endpoint = Endpoint({"old": [gone()], "fb-1": [GOOD, gone("fb-1")], "fb-2": [GOOD]})
    run("gemini/old", endpoint)
    start = len(endpoint.calls)

    value, link = run("gemini/old", endpoint)

    assert link.model == "fb-2"
    assert endpoint.models()[start:] == ["fb-1", "old", "fb-2"]


# ---------------------------------------------------------------- probes

def test_the_probe_answers_through_the_fallback_and_keeps_its_4_tuple():
    endpoint = Endpoint({"old": [gone()], "fb-1": ["ok"]})
    logs = []

    live, results, value = llm.probe_chain(
        parse_chain("gemini/old"), {"gemini": "k"},
        on_log=logs.append, client_factory=endpoint.factory,
    )

    assert live == Link("gemini", "old")  # the link as configured
    assert len(results) == 1 and len(results[0]) == 4
    label, reason, _elapsed, kind = results[0]
    assert (label, reason, kind) == ("gemini/old", "ok", "ping")
    assert endpoint.models() == ["old", "fb-1"]
    assert any("↪" in line and "fb-1" in line for line in logs)


def test_the_work_probe_swaps_too_and_the_job_inherits_the_swap():
    work = {"system": "s", "user": "u", "schema": SCHEMA, "schema_name": "r",
            "max_tokens": 50}
    endpoint = Endpoint({"old": [gone()], "fb-1": [GOOD]})

    live, results, value = llm.probe_chain(
        parse_chain("gemini/old"), {"gemini": "k"}, work=work,
        on_log=lambda *a: None, client_factory=endpoint.factory,
    )
    assert value == {"ok": 1}
    assert results[0][1:4:2] == ("ok", "work")

    start = len(endpoint.calls)
    run("gemini/old", endpoint, keys={"gemini": "k"})
    assert endpoint.models()[start:] == ["fb-1"]


# ------------------------------------------------------------- the registry

def test_every_fallback_model_names_its_own_provider_default_first():
    for name, provider in registry.PROVIDERS.items():
        assert isinstance(provider.fallback_models, tuple), name
        assert len(set(provider.fallback_models)) == len(provider.fallback_models), name


def test_only_benchmarked_models_are_shipped_as_fallbacks(monkeypatch):
    """DEC-058: a model is shipped on whether it found clips. The two below
    found the test transcript's clip 3/3 on 2026-09-24; nothing else was
    measured, so nothing else is listed."""
    monkeypatch.undo()
    shipped = {n: p.fallback_models for n, p in registry.PROVIDERS.items()
               if p.fallback_models}
    assert shipped == {
        "gemini": ("gemini-flash-lite-latest",),
        "openrouter": ("meta-llama/llama-3.3-70b-instruct",),
    }
