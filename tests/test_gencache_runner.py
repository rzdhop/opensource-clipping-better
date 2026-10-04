"""Never lose a paid generation, never pay twice for one (DEC-151..154): the
generation cache and submit journal as the chain runner uses them.

A queued fake transport stands in front of the real fal and Gemini adapters,
and a small synchronous fake stands in for the rest, so nothing leaves the
machine and nothing is spent (DEC-012). ``Books`` is the caller's
``book(entry)``: what the story side turns into a ledger row and a
``budget.record``. Without a cache the runner must behave exactly as before
(RC-A2): the first tests pin that.
"""

import base64
import copy
import json
import pathlib

import pytest

from clipping import cancel as cancel_mod
from clipping.providers import gencache, images
from clipping.providers.generation import (
    GenRequest, GenResult, NoRunnableLink, parse_generation_chain, run_generation_chain,
)
from clipping.providers.registry import Link, describe
from clipping.providers.transport import APITimeoutError, Response

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
PROMPT = "an anthropomorphic kiwi in a linen shirt"
# DEC-222: nano-banana reads GEMINI_PAID_API_KEY only, never GOOGLE_API_KEY.
ENV = {"FAL_KEY": "fk", "GOOGLE_API_KEY": "gk", "GEMINI_PAID_API_KEY": "gk", "OPENAI_API_KEY": "ok"}

APP = "fal-ai/flux/schnell"
BASE = f"https://queue.fal.run/{APP}/requests/req-1"
CDN = "https://v3.fal.media/files/x/out.png"
SUBMIT = (200, {"request_id": "req-1", "status_url": BASE + "/status", "response_url": BASE})
QUEUED = (200, {"status": "IN_QUEUE", "queue_position": 2})
RUNNING = (200, {"status": "IN_PROGRESS"})
COMPLETED = (200, {"status": "COMPLETED"})
ANSWER = (200, {"images": [{"url": CDN, "width": 1080, "height": 1920, "content_type": "image/png"}], "seed": 7})
IMAGE = (200, PNG)
FAL_HAPPY = [SUBMIT, QUEUED, RUNNING, COMPLETED, ANSWER, IMAGE]

POST = ("POST", f"https://queue.fal.run/{APP}")
STATUS = ("GET", BASE + "/status")
RESPONSE = ("GET", BASE)
DOWNLOAD = ("GET", CDN)
FAL_CALLS = [POST, STATUS, STATUS, STATUS, RESPONSE, DOWNLOAD]  # today's sequence (test_image_adapters.py:215)

# A second request id: the same request sent again after its first was voided.
BASE2 = f"https://queue.fal.run/{APP}/requests/req-2"
SUBMIT2 = (200, {"request_id": "req-2", "status_url": BASE2 + "/status", "response_url": BASE2})
STATUS2 = ("GET", BASE2 + "/status")
RESPONSE2 = ("GET", BASE2)
# fal's answer for a request it no longer holds (the walk of 2026-10-04, sh02).
PURGED = (404, {"status": "NOT_FOUND"})
# Kling LipSync's refusal of a clip with no face in it, as fal answers the request (job 312eeb365663).
NO_FACE = (422, {"detail": [{"loc": ["body", "video_url"],
                             "msg": "No face detected in the image/video. Please ensure the image/video contains "
                                    "a clearly visible face.",
                             "type": "face_detection_error", "url": "https://docs.fal.ai/errors#face_detection_error",
                             "input": "https://v3b.fal.media/files/b/0aad043b/" + "x" * 300}]})

FAL_LINK = Link("fal", "flux-schnell")
FAL_ONLY = {("image", "fal"): images.FAL}
FAL_PRICE = images.FAL.estimate(FAL_LINK, GenRequest(kind="image")).est_usd
GEMINI_PRICE = images.GEMINI.estimate(Link("gemini", "nano-banana-2"), GenRequest(kind="image")).est_usd


class FakeTransport:
    """Answers each request from a queue, in order, and records what was sent."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "body": body})
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        status, payload = answer
        if isinstance(payload, (dict, list)):
            payload = json.dumps(payload).encode()
        return Response(status, {}, payload)

    def urls(self):
        return [(c["method"], c["url"]) for c in self.calls]

    def posts(self):
        return [c for c in self.calls if c["method"] == "POST"]


class Crash(BaseException):
    """The process dying: nothing after the raise runs, no ``except Exception`` sees it."""


class Books(list):
    """The caller's ``book(entry)``. *crash* = ``"before"`` dies before the row is
    written, ``"after"`` writes it and dies before the journal is stamped."""

    def __init__(self, *, fail=None, crash=None):
        super().__init__()
        self.fail = fail
        self.crash = crash

    def __call__(self, entry):
        if self.crash == "before":
            raise Crash()
        if self.fail is not None:
            raise self.fail
        self.append(copy.deepcopy(entry))
        if self.crash == "after":
            raise Crash()


class Releases(list):
    """The caller's ``release(entry)``: what the story side turns into a
    negative ledger row and ``budget.release``."""

    def __init__(self, *, fail=None):
        super().__init__()
        self.fail = fail

    def __call__(self, entry):
        if self.fail is not None:
            raise self.fail
        self.append(copy.deepcopy(entry))


class Budget(list):
    """``budget_check``: records ``(est_usd, link)`` and accepts."""

    def __call__(self, estimate, link):
        self.append((round(getattr(estimate, "est_usd", estimate), 4), describe(link)))


class Limiter:
    def __init__(self):
        self.asked = []

    def acquire(self, provider):
        self.asked.append(provider)
        return None


class SyncAdapter:
    """One request, one answer, like Gemini or OpenAI images. Records the
    extra keywords the runner passed beyond ``credentials``/``on_log``/``transport``."""

    speaks_through_transport = True

    def __init__(self, *, fail=(), est=0.04):
        self.calls = []
        self.kwargs = []
        self.fail = list(fail)
        self.est = est

    def estimate(self, link, request):
        return self.est

    def probe(self, link, *, credentials, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **kw):
        self.calls.append(link)
        self.kwargs.append(sorted(kw))
        if self.fail:
            raise self.fail.pop(0)
        path = pathlib.Path(request.out_dir) / f"{link.provider}_{request.seed}.png"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PNG + str(request.seed).encode())
        return GenResult(provider=link.provider, model=link.model, paths=(str(path),), seed=request.seed,
                         meta={"size": "native"})


def run(chain, *, adapters, out_dir, cache=None, kind="image", seed=7, allow_paid=True, budget=None,
        limiter=None, transport=None, sleep_fn=None, time_fn=None, cancel=None, log=None, extra=None):
    log = [] if log is None else log
    request = GenRequest(kind=kind, prompt=PROMPT, seed=seed, out_dir=str(out_dir), extra=dict(extra or {}))
    kwargs = dict(env=ENV, allow_paid=allow_paid, on_log=log.append, budget_check=budget, limiter=limiter,
                  adapters=adapters, transport=transport, sleep_fn=sleep_fn or (lambda s: None),
                  time_fn=time_fn or (lambda: 0.0))
    if cancel is not None:
        kwargs["cancel"] = cancel
    if cache is not None:
        kwargs["cache"] = cache
    result, link = run_generation_chain(kind, parse_generation_chain(kind, chain), request, **kwargs)
    return result, link, log


def entry_of(cache, spec, *, kind="image", seed=7):
    provider, model = spec.split("/", 1)
    return cache.lookup(cache.key(kind, Link(provider, model), GenRequest(kind=kind, prompt=PROMPT, seed=seed)))


def fal_default_sleeps(monkeypatch):
    """What fal sleeps through its own default ``sleep_fn`` -- i.e. when the runner passes none."""
    slept = []
    monkeypatch.setitem(images.FalAdapter.generate.__kwdefaults__, "sleep_fn", slept.append)
    return slept


# ------------------------------------------------------- no cache: unchanged

def test_without_a_cache_the_runner_calls_fal_exactly_as_before(tmp_path, monkeypatch):
    adapter_slept = fal_default_sleeps(monkeypatch)
    runner_slept = []
    transport = FakeTransport(FAL_HAPPY)
    result, link, _ = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, transport=transport,
                          budget=Budget(), sleep_fn=runner_slept.append)
    assert transport.urls() == FAL_CALLS
    assert len(adapter_slept) == 3 and runner_slept == []  # fal kept its own sleep: nothing was passed
    assert result.paid is True and result.est_cost == pytest.approx(FAL_PRICE)
    assert set(result.meta) == {"request_id", "width", "height"}


def test_a_cache_given_a_seedless_image_request_leaves_the_default_path_untouched(tmp_path, monkeypatch):
    adapter_slept = fal_default_sleeps(monkeypatch)
    plain = FakeTransport(FAL_HAPPY)
    _, _, plain_log = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path / "a", transport=plain,
                          budget=Budget(), seed=None)
    books, runner_slept = Books(), []
    journaled = FakeTransport(FAL_HAPPY)
    result, _, log = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path / "b", transport=journaled,
                         budget=Budget(), seed=None, cache=gencache.GenCache(tmp_path / "gen", book=books),
                         sleep_fn=runner_slept.append)
    assert journaled.urls() == plain.urls() == FAL_CALLS
    assert len(adapter_slept) == 6 and runner_slept == []
    assert log == plain_log
    assert books == [] and not (tmp_path / "gen").exists()
    assert "booked" not in result.meta


def test_the_adapter_gets_the_journal_seams_only_when_a_cache_applies(tmp_path):
    pol = SyncAdapter()
    adapters = {("image", "pollinations"): pol}
    run("pollinations/flux", adapters=adapters, out_dir=tmp_path / "a")
    run("pollinations/flux", adapters=adapters, out_dir=tmp_path / "b", seed=None,
        cache=gencache.GenCache(tmp_path / "gen", book=Books()))
    run("pollinations/flux", adapters=adapters, out_dir=tmp_path / "c",
        cache=gencache.GenCache(tmp_path / "gen", book=Books()))
    assert pol.kwargs == [[], [], ["on_submit", "sleep_fn", "time_fn"]]


def test_fal_journals_its_request_between_the_submit_and_the_first_poll(tmp_path):
    transport = FakeTransport(FAL_HAPPY)
    seen, slept = [], []
    result = images.FAL.generate(
        FAL_LINK, GenRequest(kind="image", prompt=PROMPT, seed=7, out_dir=str(tmp_path)),
        credentials={"FAL_KEY": "fk"}, on_log=lambda *a: None, transport=transport, sleep_fn=slept.append,
        on_submit=lambda info: seen.append((len(transport.calls), len(slept), copy.deepcopy(info))))
    assert seen[0] == (1, 0, {"request_id": "req-1", "status_url": BASE + "/status", "response_url": BASE})
    calls, _, later = seen[1]
    assert calls == 5 and later["request_id"] == "req-1" and later["output"]["url"] == CDN  # before the download
    assert transport.urls() == FAL_CALLS and len(slept) == 3
    assert pathlib.Path(result.paths[0]).read_bytes() == PNG and result.meta["request_id"] == "req-1"


# ------------------------------------------------------------- kept answers

def test_a_kept_answer_makes_no_call_and_asks_no_limiter(tmp_path):
    pol, limiter, books = SyncAdapter(), Limiter(), Books()
    adapters = {("image", "pollinations"): pol}
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    first, _, _ = run("pollinations/flux", adapters=adapters, out_dir=tmp_path / "one", cache=cache, limiter=limiter)
    assert len(pol.calls) == 1 and limiter.asked == ["pollinations"]
    # A free answer is booked at $0 by the journal, as imaging.book does without one.
    assert [(b["link"], b["paid"], b["est_usd"], b["state"]) for b in books] == [("pollinations/flux", False, 0.0, "done")]
    assert first.meta["booked"]["est_usd"] == 0.0 and first.meta["cache_key"]

    second, link, log = run("pollinations/flux", adapters=adapters, out_dir=tmp_path / "two", cache=cache,
                            limiter=limiter, extra={"name": "shot_01"})
    assert len(pol.calls) == 1 and limiter.asked == ["pollinations"] and len(books) == 1
    assert link == Link("pollinations", "flux")
    assert second.meta["cached"] is True and second.meta["booked"] == first.meta["booked"]
    assert second.meta["cache_key"] == first.meta["cache_key"]
    kept = pathlib.Path(second.paths[0])
    assert kept == tmp_path / "two" / "shot_01.png"
    assert kept.read_bytes() == pathlib.Path(first.paths[0]).read_bytes()
    assert second.est_cost == 0.0 and second.seed == 7
    assert any("♻️" in line for line in log)


def test_a_kept_paid_answer_is_served_with_paid_off_and_asks_no_budget(tmp_path):
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path / "one", cache=cache,
        transport=FakeTransport(FAL_HAPPY), budget=Budget())
    budget, transport = Budget(), FakeTransport([])
    result, _, _ = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path / "two", cache=cache,
                       transport=transport, budget=budget, allow_paid=False)
    assert transport.calls == [] and budget == [] and len(books) == 1
    assert result.meta["cached"] is True and result.paid is True and result.est_cost == 0.0
    assert pathlib.Path(result.paths[0]).read_bytes() == PNG


def test_a_kept_answer_whose_file_changed_is_generated_again(tmp_path):
    pol, books = SyncAdapter(), Books()
    adapters = {("image", "pollinations"): pol}
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    run("pollinations/flux", adapters=adapters, out_dir=tmp_path / "one", cache=cache)
    [stored] = (tmp_path / "gen").glob("*.png")
    stored.write_bytes(b"not the image that was answered")
    result, _, log = run("pollinations/flux", adapters=adapters, out_dir=tmp_path / "two", cache=cache)
    assert len(pol.calls) == 2 and len(books) == 2
    assert "cached" not in result.meta
    assert any("missing or changed" in line for line in log)


# --------------------------------------------------------- resume, not resubmit

def test_a_failed_poll_is_resumed_and_never_submitted_again(tmp_path):
    transport = FakeTransport([SUBMIT, APITimeoutError("GET status timed out after 120s"), COMPLETED, ANSWER, IMAGE])
    books, budget = Books(), Budget()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    result, link, log = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache,
                            transport=transport, budget=budget)
    assert transport.urls() == [POST, STATUS, STATUS, RESPONSE, DOWNLOAD]
    assert len(transport.posts()) == 1
    assert budget == [(FAL_PRICE, "fal/flux-schnell")]
    assert [(b["state"], b["request"]["request_id"], b["est_usd"], b["note"]) for b in books] == [
        ("submitted", "req-1", FAL_PRICE, None)]
    assert result.meta["resumed"] is True and result.meta["booked"]["est_usd"] == pytest.approx(FAL_PRICE)
    assert result.paid is True and result.est_cost == pytest.approx(FAL_PRICE)
    assert pathlib.Path(result.paths[0]).read_bytes() == PNG
    assert entry_of(cache, "fal/flux-schnell")["state"] == "done"
    assert any("↩️" in line and "req-1" in line for line in log)


def test_a_request_still_pending_is_kept_and_the_next_run_resumes_it_ungated(tmp_path):
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    first = FakeTransport([SUBMIT, APITimeoutError("status timed out"), APITimeoutError("status timed out again")])
    with pytest.raises(NoRunnableLink) as excinfo:
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, transport=first, budget=Budget())
    assert "kept for the next run" in str(excinfo.value)
    assert first.urls() == [POST, STATUS, STATUS]
    assert entry_of(cache, "fal/flux-schnell")["state"] == "submitted" and len(books) == 1

    def refuse(estimate, link):
        raise AssertionError("a resume is never gated again")

    second = FakeTransport([COMPLETED, ANSWER, IMAGE])
    result, _, _ = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, transport=second,
                       budget=refuse, allow_paid=False)
    assert second.urls() == [STATUS, RESPONSE, DOWNLOAD] and len(books) == 1
    assert result.meta["resumed"] is True
    assert entry_of(cache, "fal/flux-schnell")["state"] == "done"


def test_a_request_past_its_poll_budget_is_kept_submitted(tmp_path):
    clock = {"now": 0.0}

    def sleep(seconds):
        clock["now"] += seconds

    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    transport = FakeTransport([SUBMIT] + [RUNNING] * 200)
    with pytest.raises(NoRunnableLink) as excinfo:
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, transport=transport,
            budget=Budget(), sleep_fn=sleep, time_fn=lambda: clock["now"])
    assert "still IN_PROGRESS" in str(excinfo.value) and "kept for the next run" in str(excinfo.value)
    assert len(transport.posts()) == 1 and len(books) == 1
    assert entry_of(cache, "fal/flux-schnell")["state"] == "submitted"


def test_a_failed_image_download_is_resumed_from_the_journaled_output_url(tmp_path):
    transport = FakeTransport([SUBMIT, COMPLETED, ANSWER, APITimeoutError("GET cdn timed out"), IMAGE])
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    result, _, _ = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache,
                       transport=transport, budget=Budget())
    assert transport.urls() == [POST, STATUS, RESPONSE, DOWNLOAD, DOWNLOAD]
    assert len(books) == 1 and result.meta["resumed"] is True
    assert entry_of(cache, "fal/flux-schnell")["request"]["output"]["url"] == CDN


def test_a_cancel_mid_poll_keeps_the_request_submitted_for_the_next_run(tmp_path):
    token = cancel_mod.CancelToken()
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    transport = FakeTransport([SUBMIT, RUNNING, COMPLETED, ANSWER, IMAGE])
    with pytest.raises(cancel_mod.Cancelled):
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, transport=transport,
            budget=Budget(), sleep_fn=lambda s: token.cancel(), cancel=token)
    assert transport.urls() == [POST]  # the first poll's wait was cut short
    entry = entry_of(cache, "fal/flux-schnell")
    assert entry["state"] == "submitted" and entry["booked"]["est_usd"] == pytest.approx(FAL_PRICE)
    assert len(books) == 1

    result, _, _ = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache,
                       transport=FakeTransport([COMPLETED, ANSWER, IMAGE]), budget=Budget())
    assert result.meta["resumed"] is True and len(books) == 1


# ------------------------------------------------ settled, lost, stays booked

@pytest.mark.parametrize("answer,state", [
    ((200, {"status": "FAILED", "error": "content policy"}), "failed"),
    ((200, {"status": "CANCELLED"}), "failed"),
    ((410, {"detail": "Request expired"}), "lost"),
], ids=["failed", "cancelled", "410"])
def test_a_settled_or_lost_request_stays_booked_the_link_ends_and_the_next_link_is_gated(tmp_path, answer, state):
    transport = FakeTransport([SUBMIT, answer])
    openai = SyncAdapter(est=0.04)
    books, budget = Books(), Budget()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    result, link, log = run("fal/flux-schnell,openai/gpt-image-2-low",
                            adapters={("image", "fal"): images.FAL, ("image", "openai"): openai},
                            out_dir=tmp_path, cache=cache, transport=transport, budget=budget)
    assert link == Link("openai", "gpt-image-2-low") and len(openai.calls) == 1
    assert transport.urls() == [POST, STATUS]  # no second submit, no swap
    assert budget == [(FAL_PRICE, "fal/flux-schnell"), (0.04, "openai/gpt-image-2-low")]
    assert [(b["link"], b["est_usd"]) for b in books] == [("fal/flux-schnell", FAL_PRICE),
                                                          ("openai/gpt-image-2-low", 0.04)]
    entry = entry_of(cache, "fal/flux-schnell")
    assert entry["state"] == state and entry["booked"]["est_usd"] == pytest.approx(FAL_PRICE)
    assert any("stays booked" in line for line in log)


# ------------------------------------------- void: proven never run (F1, F2)

def test_a_purged_404_request_is_voided_and_resubmitted_once_in_the_same_run(tmp_path):
    transport = FakeTransport([SUBMIT, PURGED, SUBMIT2, COMPLETED, ANSWER, IMAGE])
    books, releases, budget = Books(), Releases(), Budget()
    cache = gencache.GenCache(tmp_path / "gen", book=books, release=releases)
    result, link, log = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache,
                            transport=transport, budget=budget)
    assert link == FAL_LINK and pathlib.Path(result.paths[0]).read_bytes() == PNG
    assert transport.urls() == [POST, STATUS, POST, STATUS2, RESPONSE2, DOWNLOAD]
    # The re-send is a new submit: gated again and booked again (RC-A3), the first booking given back.
    assert budget == [(FAL_PRICE, "fal/flux-schnell")] * 2
    assert [(b["state"], b["request"]["request_id"]) for b in books] == [("submitted", "req-1"),
                                                                         ("submitted", "req-2")]
    assert [(r["state"], r["request"]["request_id"], r["booked"]["est_usd"]) for r in releases] == [
        ("void", "req-1", pytest.approx(FAL_PRICE))]
    assert "HTTP 404" in releases[0]["note"] and "never run" in releases[0]["note"]
    assert result.paid is True and result.meta["booked"]["est_usd"] == pytest.approx(FAL_PRICE)
    entry = entry_of(cache, "fal/flux-schnell")
    assert entry["state"] == "done" and entry["request"]["request_id"] == "req-2"
    events = [a["event"] for a in entry["attempts"]]
    assert events == ["sending", "submitted", "booked", "void", "released", "sending", "submitted", "booked", "done"]
    assert f"   ↩ fal/flux-schnell: request req-1 was never run by the provider (HTTP 404); its booking of " \
           f"${FAL_PRICE:.3f} is released" in log
    assert not any("stays booked" in line for line in log)


def test_a_purged_request_found_by_the_next_run_is_voided_and_sent_again_only_through_the_gates(tmp_path):
    books, releases = Books(), Releases()
    cache = gencache.GenCache(tmp_path / "gen", book=books, release=releases)
    with pytest.raises(NoRunnableLink):
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, budget=Budget(),
            transport=FakeTransport([SUBMIT, APITimeoutError("status timed out"), APITimeoutError("again")]))
    assert entry_of(cache, "fal/flux-schnell")["state"] == "submitted" and len(books) == 1

    # The next run, paid off: the purged request is voided and released, and nothing is sent again.
    later = FakeTransport([PURGED])
    with pytest.raises(NoRunnableLink) as excinfo:
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, transport=later,
            budget=Budget(), allow_paid=False)
    assert later.urls() == [STATUS] and "allow_paid is off" in str(excinfo.value)
    assert len(books) == 1 and [r["request"]["request_id"] for r in releases] == ["req-1"]
    assert entry_of(cache, "fal/flux-schnell")["state"] == "void"

    # Paid on: a void entry is a fresh call, gated and booked; nothing is released twice.
    budget = Budget()
    result, _, _ = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, budget=budget,
                       transport=FakeTransport([SUBMIT2, COMPLETED, ANSWER, IMAGE]))
    assert budget == [(FAL_PRICE, "fal/flux-schnell")] and len(books) == 2 and len(releases) == 1
    assert "resumed" not in result.meta and entry_of(cache, "fal/flux-schnell")["state"] == "done"


def test_a_request_purged_again_after_its_one_re_send_ends_the_link(tmp_path):
    transport = FakeTransport([SUBMIT, PURGED, SUBMIT2, (404, {"status": "NOT_FOUND"})])
    openai = SyncAdapter(est=0.04)
    books, releases, budget = Books(), Releases(), Budget()
    cache = gencache.GenCache(tmp_path / "gen", book=books, release=releases)
    _result, link, log = run("fal/flux-schnell,openai/gpt-image-2-low",
                             adapters={("image", "fal"): images.FAL, ("image", "openai"): openai},
                             out_dir=tmp_path, cache=cache, transport=transport, budget=budget, log=[])
    assert link == Link("openai", "gpt-image-2-low")
    assert transport.urls() == [POST, STATUS, POST, STATUS2]  # sent again once, never a third time
    assert [r["request"]["request_id"] for r in releases] == ["req-1", "req-2"]
    assert [b["link"] for b in books] == ["fal/flux-schnell", "fal/flux-schnell", "openai/gpt-image-2-low"]
    assert entry_of(cache, "fal/flux-schnell")["state"] == "void"
    assert sum(1 for line in log if "sending the request again, once" in line) == 1


def test_a_422_refusal_voids_the_request_releases_its_booking_and_is_not_sent_again(tmp_path):
    transport = FakeTransport([SUBMIT, COMPLETED, NO_FACE])
    books, releases = Books(), Releases()
    cache = gencache.GenCache(tmp_path / "gen", book=books, release=releases)
    log = []
    with pytest.raises(NoRunnableLink) as excinfo:
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, transport=transport,
            budget=Budget(), log=log)
    assert transport.urls() == [POST, STATUS, RESPONSE]  # one submit: the same input would be refused again
    assert len(books) == 1 and [r["request"]["request_id"] for r in releases] == ["req-1"]
    entry = entry_of(cache, "fal/flux-schnell")
    assert entry["state"] == "void" and entry["released"]["est_usd"] == pytest.approx(FAL_PRICE)
    (label, reason), = excinfo.value.failures
    # The provider's error type survives a detail cut at 300 characters.
    assert label == "fal/flux-schnell" and "[error type: face_detection_error]" in reason
    assert "refused, never run" in reason and "released" in reason
    assert "kept for the next run" not in reason and "stays booked" not in reason
    assert any(line.startswith("   ↩ fal/flux-schnell: request req-1 was never run by the provider (HTTP 422)")
               for line in log)


def test_a_404_after_the_request_completed_stays_lost_and_booked(tmp_path):
    # Not from the status URL: the provider ran it (COMPLETED), so it may be billed.
    transport = FakeTransport([SUBMIT, COMPLETED, (404, {"detail": "Request not found"})])
    books, releases = Books(), Releases()
    cache = gencache.GenCache(tmp_path / "gen", book=books, release=releases)
    with pytest.raises(NoRunnableLink) as excinfo:
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, transport=transport,
            budget=Budget())
    assert transport.urls() == [POST, STATUS, RESPONSE] and len(books) == 1 and releases == []
    assert entry_of(cache, "fal/flux-schnell")["state"] == "lost" and "stays booked" in str(excinfo.value)


@pytest.mark.parametrize("answer", [(401, {"detail": "invalid key"}), (403, {"detail": "forbidden"}),
                                    (429, {"detail": "slow down"})], ids=["401", "403", "429"])
def test_a_refused_poll_proves_nothing_about_the_request_and_is_never_voided(tmp_path, answer):
    transport = FakeTransport([SUBMIT, answer, answer])
    books, releases = Books(), Releases()
    cache = gencache.GenCache(tmp_path / "gen", book=books, release=releases)
    with pytest.raises(NoRunnableLink):
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache, transport=transport,
            budget=Budget())
    assert len(transport.posts()) == 1 and len(books) == 1 and releases == []
    assert entry_of(cache, "fal/flux-schnell")["state"] == "submitted"


def test_a_void_without_a_release_hook_or_with_a_failing_one_stays_booked(tmp_path):
    for releases in (None, Releases(fail=OSError(28, "No space left on device"))):
        root = tmp_path / ("none" if releases is None else "failing")
        books, log = Books(), []
        cache = gencache.GenCache(root / "gen", book=books, release=releases)
        with pytest.raises(NoRunnableLink):
            run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=root, cache=cache, budget=Budget(), log=log,
                transport=FakeTransport([SUBMIT, COMPLETED, NO_FACE]))
        entry = entry_of(cache, "fal/flux-schnell")
        assert entry["state"] == "void" and entry["booked"] is not None and "released" not in entry
        assert any("stays (it could not be released)" in line for line in log)


@pytest.mark.parametrize("after", [
    [(200, {"status": "FAILED", "error": "content policy"})],
    [(401, {"detail": "invalid key"})],
    [APITimeoutError("status timed out"), APITimeoutError("status timed out")],
], ids=["failed", "401", "two-timeouts"])
def test_a_failure_after_the_submit_is_booked_once_before_the_error_surfaces(tmp_path, after):
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    with pytest.raises(NoRunnableLink):
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache,
            transport=FakeTransport([SUBMIT] + after), budget=Budget())
    assert [(b["link"], b["request"]["request_id"], b["est_usd"]) for b in books] == [
        ("fal/flux-schnell", "req-1", FAL_PRICE)]


@pytest.mark.parametrize("crash,booked", [("before", 1), ("after", 2)])
def test_a_crash_between_journal_and_booking_over_books_by_one_at_most_never_zero(tmp_path, crash, booked):
    books = Books(crash=crash)
    with pytest.raises(Crash):
        run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path,
            cache=gencache.GenCache(tmp_path / "gen", book=books), transport=FakeTransport([SUBMIT]),
            budget=Budget())
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    entry = entry_of(cache, "fal/flux-schnell")
    assert entry["state"] == "submitted" and entry["booked"] is None
    books.crash = None
    transport = FakeTransport([COMPLETED, ANSWER, IMAGE])
    result, _, _ = run("fal/flux-schnell", adapters=FAL_ONLY, out_dir=tmp_path, cache=cache,
                       transport=transport, budget=Budget())
    assert len(books) == booked  # the crash cost one extra row at most, never a missing one
    assert transport.posts() == [] and result.meta["resumed"] is True
    assert entry_of(cache, "fal/flux-schnell")["booked"] is not None


# --------------------------------------------- synchronous paid calls (DEC-153)

def gemini_image():
    data = base64.b64encode(PNG).decode()
    return (200, {"candidates": [{"content": {"parts": [{"inlineData": {"mimeType": "image/png", "data": data}}]}}]})


@pytest.mark.parametrize("answer,billed,words", [
    ((500, {"error": {"message": "internal"}}), True, "HTTP 500"),
    ((503, {"error": {"message": "overloaded"}}), True, "HTTP 503"),
    (APITimeoutError("POST generateContent timed out after 120s"), True, "timeout"),
    ((200, {"candidates": [{"finishReason": "SAFETY", "content": {"parts": []}}]}), True, "no usable answer"),
    ((400, {"error": {"message": "bad request"}}), False, "HTTP 400"),
    ((401, {"error": {"message": "bad key"}}), False, "HTTP 401"),
    ((403, {"error": {"message": "forbidden"}}), False, "HTTP 403"),
    ((409, {"error": {"message": "conflict"}}), False, "HTTP 409"),
    ((413, {"error": {"message": "too large"}}), False, "HTTP 413"),
    ((422, {"error": {"message": "unprocessable"}}), False, "HTTP 422"),
    ((429, {"error": {"message": "quota"}}), False, "HTTP 429"),
], ids=["500", "503", "timeout", "no-image", "400", "401", "403", "409", "413", "422", "429"])
def test_a_sync_paid_call_is_booked_unless_a_4xx_proves_it_unbilled(tmp_path, answer, billed, words):
    transport = FakeTransport([answer])
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    with pytest.raises(NoRunnableLink):
        run("gemini/nano-banana-2", adapters={("image", "gemini"): images.GEMINI}, out_dir=tmp_path, cache=cache,
            transport=transport, budget=Budget())
    assert len(transport.calls) == 1  # one attempt per paid link (DEC-106)
    entry = entry_of(cache, "gemini/nano-banana-2")
    assert entry["state"] == "failed" and words in entry["note"]
    if billed:
        assert [(b["est_usd"], words in b["note"]) for b in books] == [(GEMINI_PRICE, True)]
        assert entry["booked"]["note"] == entry["note"]
    else:
        assert books == [] and entry["booked"] is None


def test_a_sync_paid_answer_is_booked_once_by_the_journal(tmp_path):
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    result, _, _ = run("gemini/nano-banana-2", adapters={("image", "gemini"): images.GEMINI}, out_dir=tmp_path,
                       cache=cache, transport=FakeTransport([gemini_image()]), budget=Budget())
    assert [(b["link"], b["est_usd"], b["note"]) for b in books] == [("gemini/nano-banana-2", GEMINI_PRICE, None)]
    assert result.meta["booked"]["est_usd"] == GEMINI_PRICE and result.est_cost == GEMINI_PRICE
    entry = entry_of(cache, "gemini/nano-banana-2")
    assert entry["state"] == "done" and [f["sha256"] for f in entry["output"]["files"]]


def test_a_paid_call_that_failed_before_sending_is_unbilled_unless_the_adapter_cannot_prove_it(tmp_path):
    books = Books()
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    transport = FakeTransport([])
    with pytest.raises(NoRunnableLink):  # seedream needs a reference: refused before the POST
        run("fal/seedream-4-edit", kind="image_edit", adapters={("image_edit", "fal"): images.FAL},
            out_dir=tmp_path, cache=cache, transport=transport, budget=Budget())
    assert transport.calls == [] and books == []
    entry = entry_of(cache, "fal/seedream-4-edit", kind="image_edit")
    assert entry["state"] == "failed" and "before sending" in entry["note"]

    sdk = SyncAdapter(fail=[ValueError("bad size")])
    sdk.speaks_through_transport = False  # an SDK sends; its failure never proves nothing went out
    with pytest.raises(NoRunnableLink):
        run("openai/gpt-image-2-low", adapters={("image", "openai"): sdk}, out_dir=tmp_path, cache=cache,
            budget=Budget())
    assert [(b["link"], "no usable answer" in b["note"]) for b in books] == [("openai/gpt-image-2-low", True)]


@pytest.mark.parametrize("allow_paid", [True, False], ids=["paid-on", "paid-off"])
def test_a_call_the_process_died_in_is_booked_as_lost_then_gated_as_today(tmp_path, allow_paid):
    books = Books()
    dying = SyncAdapter(fail=[Crash()])
    with pytest.raises(Crash):
        run("openai/gpt-image-2-low", adapters={("image", "openai"): dying}, out_dir=tmp_path,
            cache=gencache.GenCache(tmp_path / "gen", book=books), budget=Budget())
    cache = gencache.GenCache(tmp_path / "gen", book=books)
    assert entry_of(cache, "openai/gpt-image-2-low")["state"] == "sending" and books == []

    fresh, budget, log = SyncAdapter(), Budget(), []
    try:
        run("openai/gpt-image-2-low", adapters={("image", "openai"): fresh}, out_dir=tmp_path, cache=cache,
            budget=budget, allow_paid=allow_paid, log=log)
    except NoRunnableLink as exc:
        assert not allow_paid and "allow_paid is off" in str(exc)
    assert (books[0]["state"], books[0]["est_usd"], books[0]["note"]) == ("sending", 0.04, gencache.NOTE_CRASH)
    events = [a["event"] for a in entry_of(cache, "openai/gpt-image-2-low")["attempts"]]
    if allow_paid:
        assert budget == [(0.04, "openai/gpt-image-2-low")] and len(fresh.calls) == 1
        assert [(b["state"], b["note"]) for b in books[1:]] == [("done", None)]
        assert events == ["sending", "booked", "lost", "sending", "done", "booked"]
    else:
        assert budget == [] and fresh.calls == [] and len(books) == 1
        assert events == ["sending", "booked", "lost"]


# ---------------------------------------------- a journal that cannot be written

@pytest.mark.parametrize("broken", ["booking", "journal"])
def test_a_submit_that_cannot_be_journaled_or_booked_stops_the_chain(tmp_path, monkeypatch, broken):
    books = Books(fail=RuntimeError("cost_ledger.json cannot be read")) if broken == "booking" else Books()
    if broken == "journal":
        real = gencache._atomic_write_json

        def write(path, data):
            if data.get("state") == "submitted":
                raise OSError(28, "No space left on device")
            real(path, data)

        monkeypatch.setattr(gencache, "_atomic_write_json", write)
    openai = SyncAdapter()
    transport = FakeTransport([SUBMIT, COMPLETED, ANSWER, IMAGE])
    log = []
    with pytest.raises(gencache.JournalError) as excinfo:
        run("fal/flux-schnell,openai/gpt-image-2-low",
            adapters={("image", "fal"): images.FAL, ("image", "openai"): openai}, out_dir=tmp_path,
            cache=gencache.GenCache(tmp_path / "gen", book=books), transport=transport, budget=Budget(), log=log)
    assert openai.calls == []  # the chain stops: no other link is tried
    assert transport.urls() == [POST]  # not even a poll
    message = str(excinfo.value)
    assert "req-1" in message and BASE + "/status" in message
    assert any("🛑" in line and "req-1" in line and BASE + "/status" in line and BASE in line for line in log)
    if broken == "journal":
        assert len(books) == 1  # the money is recorded even though the journal is not
