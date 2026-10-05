"""``tools/bench_llm.py --episode-ab`` (plan 23 stage D7): the writing-v3 chain
of one episode (E1v3, E2v3 per body scene, E3v3, J1v3) once per link, each link
alone, the scripts side by side -- and the one place the bench may spend, under
``--allow-paid`` and ``--max-usd`` (DEC-115's explicit exception).

Every request goes through the real ``llm.run_chain`` and the script step's own
methods, with ``llm.build_client`` replaced by a fake provider that answers each
prompt from the canned replies of the writing-v3 tests (no network; the keys
below are test values). The story is the v3 fixture of
``test_story_prompts_v3`` under ``tmp_path``; today's spend is a ``spend.json``
there too. Nothing here reaches a real key, a real story or the repository's
``data/``: the shared ``hermetic`` fixture checks that again at the end.

Stdlib + pytest (DEC-012).
"""

from __future__ import annotations

import datetime
import functools
import importlib
import json
import os
from types import SimpleNamespace

import pytest

import test_story_episode_steps as eps
import test_story_prompts_v3 as v3
from clipping.aistory import prompts
from clipping.aistory.ledger import CostLedger
from clipping.providers import anthropic_llm, budget, llm, pacing, registry
from test_story_episode_steps import hermetic, store  # noqa: F401 -- fixtures, used as they are

FREE = "gemini/gemini-test"
PAID = "gemini-paid/gemini-3.8-flash"
PAID_TWO = "openrouter/mistralai/mistral-small-3.2-24b-instruct"
# Test values only: every request goes to the fake provider.
KEYS = {"GOOGLE_API_KEY": "test-gemini-key", "GEMINI_PAID_API_KEY": "test-gemini-paid-key",
        "OPENROUTER_API_KEY": "test-openrouter-key"}
PAID_ON = dict(KEYS, ALLOW_PAID="1", DAILY_CAP_USD="50", PER_STORY_CAP_USD="50", PER_EPISODE_CAP_USD="50")
PAID_OFF = dict(KEYS)
# The reply ids by the name of their schema, as the request carries it.
IDS = {name: prompt for prompt, name in prompts.SCHEMA_NAMES.items()}


REAL_BUILD = llm.build_client  # before any test replaces it


def _bench():
    return importlib.import_module("tools.bench_llm")


class FakeProvider:
    """``llm.build_client``'s stand-in: answers each request with the canned
    reply of its prompt and records it (``sent``: ``(link, prompt id)``).
    every reply bills what a request that size at its cap would (the prompt's own tokens in, *out_share* of
    the request's ``max_tokens`` out), so a booking never beats the meter's estimate of the request;
    *bad_first* the prompt ids whose first reply is unusable (asked again);
    *interrupt_at* the request number that raises ``KeyboardInterrupt``."""

    def __init__(self, out_share=0.25, bad_first=(), interrupt_at=None):
        self.sent = []
        self.out_share = out_share
        self.bad_first = list(bad_first)
        self.interrupt_at = interrupt_at

    def __call__(self, link, *, api_key, timeout, **_extra):
        create = functools.partial(self._create, link)
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def _reply(self, call):
        prompt = call["prompt"]
        if prompt == "E1v3":
            return v3.E1V3_REPLY
        if prompt == "E2v3":
            return v3.e2_v3_reply(call)
        if prompt == "E3v3":
            return eps.E3_FULL
        if prompt == "J1v3":
            return eps.J1_PASSED
        raise AssertionError(f"the bench asked for {prompt}, which is not part of the writing-v3 chain")

    def _create(self, link, **body):
        schema = body["response_format"]["json_schema"]
        call = {"prompt": IDS[schema["name"]], "schema": schema["schema"], "user": body["messages"][-1]["content"]}
        self.sent.append((f"{link.provider}/{link.model}", call["prompt"]))
        if self.interrupt_at is not None and len(self.sent) == self.interrupt_at:
            raise KeyboardInterrupt
        if call["prompt"] in self.bad_first:
            self.bad_first.remove(call["prompt"])
            content = json.dumps({"lines": [], "sfx_cues": [], "on_screen_text": None})   # valid shape, no lines
        else:
            content = json.dumps(self._reply(call))
        prompt_tokens = pacing.estimate_tokens(*(message["content"] for message in body["messages"]))
        completion_tokens = int(body["max_tokens"] * self.out_share)
        reply_usage = SimpleNamespace(prompt_tokens=prompt_tokens, completion_tokens=completion_tokens,
                                      total_tokens=prompt_tokens + completion_tokens)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
                               usage=reply_usage)

    def paid(self):
        return [item for item in self.sent if item[0] != FREE]


class NoLimiter:
    """A provider's free-tier pacing (10 requests a minute on the free Gemini link) would sleep through a
    test's dozens of fake requests; the fake provider has no rate."""

    def acquire(self, est_tokens=0):
        return None

    def record(self, actual_tokens):
        return None


@pytest.fixture
def ab(store, monkeypatch):
    """A ready v3 story, the fake provider, and a way to run the bench on it."""
    monkeypatch.setattr(pacing, "limiter_for", lambda provider, **_kwargs: NoLimiter())
    story_id = eps._ready_story(store, v2=True, writing="v3")
    story_dir = store.story_dir(story_id)
    fake = FakeProvider()
    monkeypatch.setattr(llm, "build_client", fake)
    bench = _bench()
    lines = []

    def run(chains, *, settings=PAID_ON, fake_provider=None, **kwargs):
        if fake_provider is not None:
            monkeypatch.setattr(llm, "build_client", fake_provider)
        kwargs.setdefault("now", datetime.datetime(2026, 10, 5, 9, 0, 0, tzinfo=datetime.timezone.utc))
        return bench.run_episode_ab(story_dir, chains, ep=1, settings_env=dict(settings), out=lines.append,
                                    on_log=lambda line: None, **kwargs)

    return SimpleNamespace(bench=bench, story_id=story_id, story_dir=story_dir, fake=fake, run=run, lines=lines,
                           ledger=CostLedger(os.path.join(story_dir, "cost_ledger.json")),
                           story_bytes=lambda: eps._story_bytes(store, story_id))


def _ledger_rows(ab):
    return [row for row in ab.ledger.entries()]


def _bench_dir(ab):
    root = os.path.join(ab.story_dir, "bench")
    return root, (sorted(os.listdir(root)) if os.path.isdir(root) else [])


# ------------------------------------------------------------------ dry run

def test_a_dry_run_prints_every_links_estimate_and_calls_nothing(ab):
    summary = ab.run(f"{PAID},{PAID_TWO},{FREE}", allow_paid=True, max_usd=2.5, dry_run=True)

    assert ab.fake.sent == []
    assert _bench_dir(ab)[1] == []                      # nothing written into the story
    assert ab.ledger.entries() == [] and budget.day_spent() == 0.0
    text = "\n".join(ab.lines)
    for link in (PAID, PAID_TWO, FREE):
        assert link in text
    assert "run total" in text and "Dry run: nothing was sent" in text
    assert "paid spend today" in text                    # the live today block
    rows = {row["link"]: row for row in summary["rows"]}
    assert rows[PAID]["est_usd"] > 0 and rows[PAID]["upper_usd"] == pytest.approx(2 * rows[PAID]["est_usd"])
    assert rows[FREE]["est_usd"] == 0.0
    # The estimate is the sum of the chain's calls: E1v3, one E2v3 per body scene, E3v3, J1v3.
    assert summary["estimate_usd"] == pytest.approx(sum(row["est_usd"] for row in rows.values()))


def test_the_cli_dry_run_exits_zero_and_prints_the_table(ab, monkeypatch, capsys):
    bench = ab.bench
    monkeypatch.setattr(bench, "_load_settings_env", lambda settings_file=None: dict(PAID_ON))

    code = bench.main(["--episode-ab", ab.story_dir, "--chains", f"{PAID},{FREE}", "--allow-paid",
                       "--max-usd", "2.50", "--dry-run"])

    shown = capsys.readouterr().out
    assert code == 0 and ab.fake.sent == []
    assert PAID in shown and "Dry run" in shown and "--max-usd $2.50" in shown


# --------------------------------------------------------- the paid switch

def test_without_allow_paid_the_paid_links_are_listed_and_skipped_and_the_free_one_runs(ab):
    summary = ab.run(f"{FREE},{PAID},{PAID_TWO}", settings=PAID_ON)

    assert {link for link, _prompt in ab.fake.sent} == {FREE}
    rows = {row["link"]: row for row in summary["rows"]}
    assert rows[FREE]["status"] == "complete" and rows[FREE]["real_usd"] == 0.0
    for link in (PAID, PAID_TWO):
        assert rows[link]["status"] == "skipped" and "--allow-paid not given" in rows[link]["reason"]
    assert ab.ledger.entries() == [] and budget.day_spent() == 0.0   # nothing was booked: nothing was paid
    assert "skipped" in "\n".join(ab.lines)


def test_a_paid_link_needs_a_max_usd(ab):
    with pytest.raises(ab.bench.AbRefused, match="--max-usd"):
        ab.run(PAID, allow_paid=True)
    assert ab.fake.sent == []


def test_allow_paid_while_settings_has_it_off_refuses_before_any_request(ab, monkeypatch, capsys):
    with pytest.raises(ab.bench.AbRefused, match="Settings"):
        ab.run(f"{FREE},{PAID}", settings=PAID_OFF, allow_paid=True, max_usd=2.5)
    assert ab.fake.sent == [] and _bench_dir(ab)[1] == []

    monkeypatch.setattr(ab.bench, "_load_settings_env", lambda settings_file=None: dict(PAID_OFF))
    code = ab.bench.main(["--episode-ab", ab.story_dir, "--chains", f"{FREE},{PAID}", "--allow-paid",
                          "--max-usd", "2.50"])
    assert code == 2 and "allow_paid is off in Settings" in capsys.readouterr().out
    assert ab.fake.sent == []


def test_a_link_with_no_key_is_skipped_and_named(ab):
    summary = ab.run(f"{PAID},{FREE}", settings={"GOOGLE_API_KEY": "test-gemini-key", "ALLOW_PAID": "1"},
                     allow_paid=True, max_usd=2.5)
    rows = {row["link"]: row for row in summary["rows"]}
    assert rows[PAID]["status"] == "skipped" and "GEMINI_PAID_API_KEY" in rows[PAID]["reason"]
    assert rows[FREE]["status"] == "complete"


# ------------------------------------------------------------- the cap

def test_max_usd_refuses_the_call_that_would_cross_it_and_the_summary_says_so(ab):
    # Each reply bills what its request was estimated at (~$0.012-0.023): the second request is the one the
    # $0.03 cap refuses.
    fake = FakeProvider(out_share=1.0)

    summary = ab.run(PAID, allow_paid=True, max_usd=0.03, fake_provider=fake)

    row = summary["rows"][0]
    assert row["status"] == "refused" and "--max-usd $0.03" in row["reason"]
    assert 1 <= len(fake.sent) < 7                                    # the refused request was never sent
    assert fake.sent[0][1] == "E1v3"
    assert summary["booked_usd"] <= 0.03 and summary["booked_usd"] == pytest.approx(row["real_usd"])
    assert summary["refusals"] and summary["refusals"][0]["by"] == "max-usd"
    root, stamps = _bench_dir(ab)
    markdown = open(os.path.join(root, stamps[0], "summary.md"), encoding="utf-8").read()
    assert "## Refused" in markdown and "--max-usd $0.03" in markdown
    # What was answered is booked, and only that.
    assert len(_ledger_rows(ab)) == len(fake.sent) and budget.day_spent() == pytest.approx(summary["booked_usd"])
    assert row["verdict"] == "not judged"


def test_the_cap_is_for_the_whole_run_not_each_link(ab):
    fake = FakeProvider(out_share=1.0)
    summary = ab.run(f"{PAID},{PAID_TWO}", allow_paid=True, max_usd=0.03, fake_provider=fake)

    assert [row["status"] for row in summary["rows"]][0] == "refused"
    assert summary["booked_usd"] <= 0.03                               # both links together
    assert summary["booked_usd"] == pytest.approx(sum(row["real_usd"] for row in summary["rows"]))
    assert len(fake.paid()) == len(_ledger_rows(ab))                   # every answered request booked once


def test_a_live_cap_still_refuses_what_max_usd_allows(ab):
    # The Settings' daily cap is lower than the run's own cap: budget.check refuses, nothing is sent.
    tight = dict(PAID_ON, DAILY_CAP_USD="0.01")
    summary = ab.run(PAID, settings=tight, allow_paid=True, max_usd=2.5)

    assert ab.fake.sent == []
    assert summary["rows"][0]["status"] == "refused" and summary["refusals"][0]["by"].startswith("live cap")


# ----------------------------------------------------------- the booking

def test_every_answered_call_is_booked_once_on_spend_and_the_story_ledger_under_bench(ab):
    summary = ab.run(PAID, allow_paid=True, max_usd=2.5)

    row = summary["rows"][0]
    assert row["status"] == "complete" and row["verdict"] == "pass"
    assert [prompt for _link, prompt in ab.fake.sent][:1] == ["E1v3"] and ab.fake.sent[-1][1] == "J1v3"
    rows = _ledger_rows(ab)
    assert len(rows) == len(ab.fake.sent) == row["calls"]
    assert {entry["step"] for entry in rows} == {"bench"}
    assert all(entry["paid"] and entry["ep"] is None and entry["provider"] == "gemini-paid" for entry in rows)
    assert all(entry["note"].startswith("bench A/B ") for entry in rows)
    booked = round(sum(entry["est_usd"] for entry in rows), 4)
    assert booked == pytest.approx(summary["booked_usd"]) == pytest.approx(row["real_usd"])
    assert budget.day_spent() == pytest.approx(booked)
    assert row["served_models"] == ["gemini-3.8-flash"]
    assert "Booked this run" in "\n".join(ab.lines) and "After" in "\n".join(ab.lines)


def test_the_bench_never_writes_into_the_story_it_reads(ab):
    before = ab.story_bytes()
    ab.run(f"{PAID},{FREE}", allow_paid=True, max_usd=2.5)

    assert ab.story_bytes() == before
    assert not os.path.exists(os.path.join(ab.story_dir, "episodes", "ep01", "script.json"))
    assert _bench_dir(ab)[1]                                           # only bench/<ts>/ was added


# ------------------------------------------------------- the output files

def test_the_summary_has_one_row_per_link_with_the_fields_to_compare(ab):
    fake = FakeProvider(bad_first=["E2v3"])        # one reply refused: a retry, a pass rate under 100 %
    chains = f"{PAID},{PAID_TWO},{FREE}"
    summary = ab.run(chains, allow_paid=True, max_usd=2.5, fake_provider=fake)

    assert [row["link"] for row in summary["rows"]] == [PAID, PAID_TWO, FREE]
    root, stamps = _bench_dir(ab)
    assert len(stamps) == 1
    folder = os.path.join(root, stamps[0])
    assert sorted(os.listdir(folder)) == sorted(
        ["summary.json", "summary.md", "gemini-paid__gemini-3.8-flash.json",
         "openrouter__mistralai__mistral-small-3.2-24b-instruct.json", "gemini__gemini-test.json"])

    on_disk = json.load(open(os.path.join(folder, "summary.json"), encoding="utf-8"))
    assert [row["link"] for row in on_disk["rows"]] == [PAID, PAID_TWO, FREE]
    for row in on_disk["rows"]:
        for key in ("est_usd", "real_usd", "latency_s", "validator_pass_rate", "retries", "verdict", "hook",
                    "first_lines", "status"):
            assert key in row
        assert row["status"] == "complete" and row["verdict"] == "pass"
        assert row["hook"] == "Kiwilo: Ce soir, quelqu'un quitte l'île."
        assert len(row["first_lines"]) == 3 and all(": " in line for line in row["first_lines"])
    first = on_disk["rows"][0]
    # Exactly one reply was unusable on the first link: one retry, 1 refusal of N replies.
    assert first["retries"] == 1 and first["validator_pass_rate"] == pytest.approx(
        first["validator_passes"] / first["validator_replies"], abs=1e-3) and first["validator_pass_rate"] < 1
    assert on_disk["rows"][2]["real_usd"] == 0.0 and on_disk["rows"][0]["real_usd"] > 0

    markdown = open(os.path.join(folder, "summary.md"), encoding="utf-8").read()
    assert markdown.count("| gemini-paid/gemini-3.8-flash |") == 1 and "| link | status | est $ | real $ |" in markdown
    assert "Ce soir, quelqu'un quitte l'île." in markdown and "First spoken lines" in markdown
    assert PAID in "\n".join(ab.lines) and "validator" in "\n".join(ab.lines)

    # The link's own file: the script, the verdict, the per-call usage/cost/latency/retries.
    doc = json.load(open(os.path.join(folder, "gemini-paid__gemini-3.8-flash.json"), encoding="utf-8"))
    assert doc["script"]["scenes"] and doc["script"]["spine"] and doc["first_watch"]["passed"] is True
    assert [call["prompt"] for call in doc["calls"]][0] == "E1v3" and doc["calls"][-1]["prompt"] == "J1v3"
    assert all({"latency_s", "usd", "retries", "requests", "served_models", "tokens_in", "tokens_out"} <= set(call)
               for call in doc["calls"])
    assert sum(call["retries"] for call in doc["calls"]) == 1


def test_each_link_runs_alone_on_a_one_link_chain(ab):
    ab.run(f"{FREE},{PAID}", allow_paid=True, max_usd=2.5)

    by_link = {}
    for link, prompt in ab.fake.sent:
        by_link.setdefault(link, []).append(prompt)
    # The whole chain on each link, none borrowed from the other: E1v3, five-plus E2v3, E3v3, J1v3.
    for link in (FREE, PAID):
        assert by_link[link][0] == "E1v3" and by_link[link][-2:] == ["E3v3", "J1v3"] and "E2v3" in by_link[link]
    assert by_link[FREE] == by_link[PAID]


def test_a_run_cut_short_leaves_its_partial_summary(ab, monkeypatch, capsys):
    fake = FakeProvider(interrupt_at=4)            # E1v3, two E2v3 answered, the fourth request is interrupted

    summary = ab.run(PAID, allow_paid=True, max_usd=2.5, fake_provider=fake)

    assert summary["interrupted"] is True and summary["rows"][0]["status"] == "interrupted"
    root, stamps = _bench_dir(ab)
    folder = os.path.join(root, stamps[0])
    on_disk = json.load(open(os.path.join(folder, "summary.json"), encoding="utf-8"))
    assert on_disk["interrupted"] is True and on_disk["rows"][0]["calls"] == 4
    markdown = open(os.path.join(folder, "summary.md"), encoding="utf-8").read()
    assert "INTERRUPTED" in markdown and PAID in markdown
    assert os.path.exists(os.path.join(folder, "gemini-paid__gemini-3.8-flash.json"))
    # What was answered before the interrupt is booked, and only that.
    assert len(_ledger_rows(ab)) == 3 and budget.day_spent() == pytest.approx(summary["booked_usd"])

    monkeypatch.setattr(ab.bench, "_load_settings_env", lambda settings_file=None: dict(PAID_ON))
    monkeypatch.setattr(llm, "build_client", FakeProvider(interrupt_at=2))
    code = ab.bench.main(["--episode-ab", ab.story_dir, "--chains", FREE])
    assert code == 130 and "INTERRUPTED" in capsys.readouterr().out


def test_an_episode_the_season_does_not_plan_is_refused_with_the_steps_sentence(ab):
    with pytest.raises(ab.bench.AbRefused, match="season plans"):
        ab.bench.run_episode_ab(ab.story_dir, FREE, ep=9, settings_env=dict(PAID_ON), out=lambda line: None)
    assert ab.fake.sent == []


def test_a_chain_that_does_not_parse_is_refused(ab):
    with pytest.raises(ab.bench.AbRefused, match="--chains"):
        ab.run("not-a-provider/some-model")


# ------------------------------------------------- an Anthropic link, served by a fallback

def _claude_reply(request):
    """The canned writing-v3 reply of the request (named by the shape of its schema: the Messages API request
    carries no schema name)."""
    schema = request["output_config"]["format"]["schema"]
    keys = set(schema["properties"])
    if "spine" in keys:
        return v3.E1V3_REPLY
    if "lines" in keys:
        return v3.e2_v3_reply({"schema": schema, "user": request["messages"][-1]["content"]})
    if "hook" in keys:
        return eps.E3_FULL
    return eps.J1_PASSED


def test_a_link_served_by_a_fallback_is_booked_and_named_at_the_served_model(ab, monkeypatch):
    sent = []

    def create(**request):
        sent.append(request)
        usage = SimpleNamespace(input_tokens=1000, cache_read_input_tokens=0, cache_creation_input_tokens=0,
                                output_tokens=300, output_tokens_details=None, iterations=None)
        text = SimpleNamespace(type="text", text=json.dumps(_claude_reply(request)))
        if len(sent) == 1:
            # The first request is declined by Sonnet 5.5 and continued by Sonnet 5, server side.
            iterations = [
                SimpleNamespace(type="message", model=None, input_tokens=1000, cache_read_input_tokens=0,
                                cache_creation_input_tokens=0, output_tokens=40),
                SimpleNamespace(type="fallback_message", model="claude-sonnet-5", input_tokens=1000,
                                cache_read_input_tokens=0, cache_creation_input_tokens=0, output_tokens=300)]
            usage.iterations = iterations
            blocks = [SimpleNamespace(type="fallback", from_=SimpleNamespace(model="claude-sonnet-5-5"),
                                      to=SimpleNamespace(model="claude-sonnet-5")), text]
            model = "claude-sonnet-5"
        else:
            blocks, model = [text], "claude-sonnet-5-5"
        return SimpleNamespace(content=blocks, stop_reason="end_turn", stop_details=None, model=model, usage=usage)

    def sdk(**_kwargs):
        return SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(create=create)),
                               models=SimpleNamespace(retrieve=lambda model_id: SimpleNamespace(id=model_id)))

    def build(link, *, api_key, timeout, **extra):
        if registry.PROVIDERS[link.provider].api == "anthropic":
            return REAL_BUILD(link, api_key=api_key, timeout=timeout, **extra)
        raise AssertionError(f"{link} is not an Anthropic link: it must not be built")

    monkeypatch.setattr(anthropic_llm, "_sdk_client", sdk)
    monkeypatch.setattr(llm, "build_client", build)
    link = "anthropic/claude-sonnet-5-5"

    summary = ab.run(link, settings=dict(PAID_ON, ANTHROPIC_API_KEY="test-anthropic-key"), allow_paid=True,
                     max_usd=2.5)

    row = summary["rows"][0]
    assert row["status"] == "complete" and row["verdict"] == "pass"
    assert row["served_models"] == ["claude-sonnet-5", "claude-sonnet-5-5"]
    rows = _ledger_rows(ab)
    assert len(rows) == len(sent) == row["calls"]                     # booked once each, the fallback's attempts inside
    assert rows[0]["model"] == "claude-sonnet-5" and "server-side fallback" in rows[0]["note"]
    assert rows[0]["note"].startswith("bench A/B E1v3")
    assert {entry["step"] for entry in rows} == {"bench"} and all(entry["provider"] == "anthropic" for entry in rows)
    doc = json.load(open(os.path.join(summary["summary_dir"], "anthropic__claude-sonnet-5-5.json"), encoding="utf-8"))
    assert doc["calls"][0]["fallback"] is True and doc["calls"][1]["fallback"] is False
    assert doc["totals"]["fallback"] is True and doc["calls"][0]["served_models"] == ["claude-sonnet-5"]
    assert budget.day_spent() == pytest.approx(round(sum(entry["est_usd"] for entry in rows), 4))
