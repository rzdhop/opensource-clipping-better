"""The style preview strip (AI Story phase 1, stage 8; spec 3 step 4, 2.11, 8.1, 8.5).

``clipping/aistory/steps/style_preview.py`` makes three small sample images of
the story's style lock through ``IMAGE_CHAIN``: the story's route, the budget
(``allow_paid``, the caps, one attempt per paid link) and the free-tier
limiter all apply, and every answered call is booked in the story's
``cost_ledger.json``. ``web/api/routes/stories.py`` queues it as a step job,
estimates it, and serves the images behind the token.

Offline and hermetic: every provider answers through a fake transport (the
adapters' own ``urllib_transport`` and the OpenAI SDK factory are replaced by
stand-ins that fail the test), the Settings values are test values, and every
default path -- the free-tier counters, the daily spend, the outputs root, the
job store -- is redirected under ``tmp_path``. The repository's own ``data/``
files and ``outputs/stories`` are fingerprinted before and after every test.

The step, schema and estimate tests are stdlib + pytest (the CI environment,
DEC-012); the route tests need fastapi and skip without it, like the other
route tests. The new modules are imported inside the tests, so on the parent
commit each group fails on its own instead of the file failing to collect.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from clipping.aistory import prompting, schemas, steps, stylelock, templates
from clipping.aistory.ledger import CostLedger
from clipping.aistory.store import StoryStore
from clipping.cancel import Cancelled, CancelToken
from clipping.providers.generation import GenResult
from clipping.providers.transport import APIConnectionError, Response

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-26T10:00:00+00:00"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24

# Every variable a generation chain, the budget or the limiter reads; the
# machine running the tests may have any of them in its .env.
GEN_VARS = (
    "FAL_KEY", "OPENAI_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "POLLINATIONS_API_KEY",
    "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN", "TTS_CHAIN", "VISION_CHAIN",
    "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL",
    "ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE",
    "LLM_CHAIN", "ALLOW_SLOW_CHAIN", "MAX_QUEUED_JOBS",
)
REAL_FILES = tuple(ROOT / "data" / name for name in ("usage.json", "spend.json", "chain_test_ledger.json"))
REAL_STORIES = (ROOT / "outputs" / "stories", ROOT / "outputs" / "stories.json")

# Test values only: every request goes to a FakeTransport.
FAL = {"FAL_KEY": "test-fal-key"}
# DEC-222: nano-banana (the only gemini image/edit link) reads GEMINI_PAID_API_KEY only, never
# GOOGLE_API_KEY.
GEMINI = {"GOOGLE_API_KEY": "test-google-key", "GEMINI_PAID_API_KEY": "test-google-key"}
PAID_ON = {"ALLOW_PAID": "1"}
COMFY = {"LOCAL_COMFYUI_URL": "http://comfy.test:8188"}
FREE = {"IMAGE_CHAIN": "pollinations/flux"}

# $0.003 per megapixel at 576x1024, billed as one whole megapixel (fal rounds up).
FAL_PER_IMAGE = 0.003
# gemini-3.1-flash-lite-image at 1K, per image whatever the size.
GEMINI_PER_IMAGE = 0.0336


def _fingerprint(path: Path):
    if path.is_symlink() or path.exists():
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        return "present"
    return None


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    """No key, chain, cap or limit of the machine reaches a test; no request
    leaves the process; nothing is written outside ``tmp_path``."""
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env
    from clipping.providers import adapters, budget, images, limits, local_comfyui

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in GEN_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "data" / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    limits.reset()
    budget.reset()
    adapters.load_all()

    def no_network(method, url, **_kwargs):
        raise AssertionError(f"a real request was attempted: {method} {url}")

    def no_sdk(**_kwargs):
        raise AssertionError("the OpenAI SDK was reached")

    monkeypatch.setattr(images, "urllib_transport", no_network)
    monkeypatch.setattr(local_comfyui, "urllib_transport", no_network)
    monkeypatch.setattr(images, "_openai_client", no_sdk)
    monkeypatch.setattr(images, "FAL_POLL_INTERVAL_SECONDS", 0.0)

    before = {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES}
    yield
    assert limits.default_usage_path().startswith(str(tmp_path))
    assert budget.default_spend_path().startswith(str(tmp_path))
    limits.reset()
    budget.reset()
    assert {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES} == before


@pytest.fixture
def store(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


# ------------------------------------------------------------------ helpers

def _new():
    """The stage-8 step module (absent on the parent commit)."""
    from clipping.aistory.steps import style_preview

    return style_preview


class Log(list):
    def __call__(self, line):
        self.append(str(line))


class FakeTransport:
    """Answers by URL substring, first matching rule wins; records every call.
    An answer is ``(status, body)``, an exception to raise, or ``f(call)``
    returning either."""

    def __init__(self, *rules):
        self.rules = list(rules)
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        call = {"method": method, "url": url, "headers": dict(headers or {}), "body": body}
        self.calls.append(call)
        for needle, answer in self.rules:
            if needle in url:
                if callable(answer) and not isinstance(answer, BaseException):
                    answer = answer(call)
                if isinstance(answer, BaseException):
                    raise answer
                status, payload = answer
                if isinstance(payload, (dict, list)):
                    payload = json.dumps(payload).encode()
                return Response(status, {}, payload)
        raise AssertionError(f"unexpected request {method} {url}")

    def urls(self, needle=""):
        return [call["url"] for call in self.calls if needle in call["url"]]

    def posts(self, needle=""):
        return [call for call in self.calls if call["method"] == "POST" and needle in call["url"]]


def sequence(*answers):
    queue = list(answers)
    return lambda call: queue.pop(0)


POLLINATIONS_OK = ("image.pollinations.ai", (200, PNG))


def fal_rules():
    return [
        ("/status", (200, {"status": "COMPLETED"})),
        ("/requests/", (200, {"images": [{"url": "https://v3.fal.media/files/sample.png",
                                          "content_type": "image/png"}]})),
        ("fal.media", (200, PNG)),
        ("queue.fal.run", (200, {"request_id": "req-1"})),
    ]


def gemini_rules():
    image = {"inlineData": {"mimeType": "image/png", "data": base64.b64encode(PNG).decode()}}
    return [("generativelanguage.googleapis.com", (200, {"candidates": [{"content": {"parts": [image]}}]}))]


class RecordingAdapter:
    """A free image adapter that writes a file with *ext* and keeps every request."""

    def __init__(self, ext="png"):
        self.ext = ext
        self.requests = []

    def estimate(self, link, request):
        return None

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(copy.copy(request))
        path = os.path.join(request.out_dir, f"{request.extra['name']}.{self.ext}")
        with open(path, "wb") as fh:
            fh.write(PNG)
        return GenResult(provider=link.provider, model=link.model, paths=(path,), seed=request.seed)


def _no_sleep(seconds):
    raise AssertionError(f"the chain tried to sleep {seconds}s")


def _lock(overrides=None):
    return stylelock.build_style_lock(templates.load_style("fruit_drama"), overrides or {}, now=NOW)


def _story(store, *, route="auto", style=True, overrides=None):
    """A French story (its title and seed name fruit characters) with a draft
    ``fruit_drama`` lock unless *style* is off."""
    story_id = store.create(language="fr", seed_text="Mangue et Kiwi sur une île de téléréalité.",
                            style_template_id="fruit_drama", now=NOW)["story_id"]

    def edit(doc):
        doc["title"] = "L'Île des Tentafruits"
        doc["generation_profile"]["route"] = route

    store.update(story_id, edit, now=NOW)
    if style:
        store.write_doc(story_id, "style_lock.json", _lock(overrides), now=NOW,
                        validator=schemas.style_lock_errors)
    return story_id


def _ctx(store, story_id, settings, *, token=None):
    log = Log()
    ctx = steps.StepContext(
        job_id="job000000001", story_id=story_id, step="style_preview", ep=None, params={},
        cancel=token or CancelToken(), settings_env=dict(settings), outputs_dir=store.outputs_dir,
        on_log=log,
    )
    return ctx, log


def _preview(store, story_id, settings, transport, *, token=None, **kwargs):
    """Run the step; ``(outcome, log)``."""
    ctx, log = _ctx(store, story_id, settings, token=token)
    outcome = _new().run(ctx, transport=transport, sleep_fn=_no_sleep, time_fn=lambda: 100.0, **kwargs)
    return outcome, log


def _refused(store, story_id, settings, transport, **kwargs):
    """Run the step expecting a StepFailed; ``(message, log)``."""
    ctx, log = _ctx(store, story_id, settings)
    with pytest.raises(steps.StepFailed) as caught:
        _new().run(ctx, transport=transport, sleep_fn=_no_sleep, time_fn=lambda: 100.0, **kwargs)
    return str(caught.value), log


def _folder(store, story_id) -> Path:
    return Path(store.story_dir(story_id)) / "styles" / "preview"


def _ledger(store, story_id) -> list:
    path = Path(store.story_dir(story_id)) / "cost_ledger.json"
    return CostLedger(str(path)).entries() if path.exists() else []


def _rows(entries):
    return [(e["step"], e["provider"], e["model"], e["unit"], e["qty"], e["est_usd"], e["paid"], e["ep"])
            for e in entries]


def _spend(tmp_path) -> Path:
    return tmp_path / "data" / "spend.json"


def _usage(tmp_path) -> Path:
    return tmp_path / "data" / "usage.json"


# ======================================================== the free preview

def test_a_free_link_makes_three_previews_booked_at_zero_and_spends_nothing(store, tmp_path):
    m = _new()
    story_id = _story(store, overrides={"palette.accents": ["#123456"]})
    transport = FakeTransport(POLLINATIONS_OK)

    outcome, log = _preview(store, story_id, FREE, transport)

    folder = _folder(store, story_id)
    assert sorted(p.name for p in folder.iterdir()) == ["preview_1.png", "preview_2.png", "preview_3.png"]
    assert all((folder / f"preview_{n}.png").read_bytes() == PNG for n in (1, 2, 3))

    lock = store.read_doc(story_id, "style_lock.json")
    prompts = m.sample_prompts(lock)
    doc = store.read_doc(story_id, "style_preview.json")
    assert schemas.style_preview_errors(doc) == []
    assert (doc["$schema"], doc["template_id"], doc["template_version"], doc["overrides"]) == (
        "style_preview_v1", "fruit_drama", lock["template_version"], {"palette.accents": ["#123456"]})
    assert doc["images"] == [
        {"name": f"preview_{n}.png", "n": n, "link": "pollinations/flux", "seed": m.sample_seed(story_id, n),
         "paid": False, "est_usd": 0.0, "prompt": prompts[n - 1]}
        for n in (1, 2, 3)
    ]
    assert doc["failed"] == []

    # Every answered call is booked, at $0.00; the daily spend is never opened.
    assert _rows(_ledger(store, story_id)) == [
        ("style_preview", "pollinations", "flux", "image", 1, 0.0, False, None)] * 3
    assert not _spend(tmp_path).exists()
    # The free-tier limiter counted each call.
    assert json.loads(_usage(tmp_path).read_text())["providers"]["pollinations"]["calls"] == 3

    # One request per sample: tiny, 9:16, the sample's own seed and prompt.
    queries = [parse_qs(urlparse(url).query) for url in transport.urls()]
    assert [(q["width"], q["height"], q["seed"]) for q in queries] == [
        (["576"], ["1024"], [str(m.sample_seed(story_id, n))]) for n in (1, 2, 3)]
    assert [unquote(urlparse(url).path.split("/prompt/", 1)[1]) for url in transport.urls()] == prompts

    assert outcome == {"images": 3, "failed": 0, "est_usd": 0.0}
    assert log == [
        "🖼 Style preview: 3 samples of fruit_drama at 576x1024, route auto.",
        "   🔁 pollinations/flux: attempt 1/2",
        "   ✅ pollinations/flux answered in 0.0s",
        "🖼 preview 1/3 via pollinations/flux ($0.000)",
        "   🔁 pollinations/flux: attempt 1/2",
        "   ✅ pollinations/flux answered in 0.0s",
        "🖼 preview 2/3 via pollinations/flux ($0.000)",
        "   🔁 pollinations/flux: attempt 1/2",
        "   ✅ pollinations/flux answered in 0.0s",
        "🖼 preview 3/3 via pollinations/flux ($0.000)",
        "🖼 Style preview ready: 3/3 images ($0.000).",
    ]


def test_the_three_prompts_preview_the_style_and_never_the_story(store):
    m = _new()
    lock = _lock()

    prompts = m.sample_prompts(lock)

    assert prompts == [
        prompting.master_plate_prompt(lock, place_descriptor="a typical main location for this series",
                                      time_variant="day"),
        prompting.portrait_prompt(lock, descriptor="an original sample character designed for this style",
                                  signature_items=["one signature accessory"]),
        prompting.shot_prompt(lock, subjects_block="two original sample characters",
                              action="one leans in to share a secret", place_block="a set typical of this style",
                              time_variant="golden hour", framing="medium_two_shot"),
    ]
    # What a story says (French title, seed, concept names) never reaches a prompt:
    # the builders are given the lock and constants only.
    story_id = _story(store)
    transport = FakeTransport(POLLINATIONS_OK)
    _preview(store, story_id, FREE, transport)
    sent = " ".join(unquote(url) for url in transport.urls())
    for word in ("Tentafruit", "Mangue", "Kiwi", "Île", "téléréalité"):
        assert word not in sent, word


def test_each_request_is_tiny_seeded_and_carries_the_style_negative(store):
    m = _new()
    story_id = _story(store)
    adapter = RecordingAdapter()

    _preview(store, story_id, FREE, FakeTransport(), adapters={("image", "pollinations"): adapter})

    lock = store.read_doc(story_id, "style_lock.json")
    assert [(r.kind, r.width, r.height, r.seed, r.negative, r.prompt) for r in adapter.requests] == [
        ("image", 576, 1024, m.sample_seed(story_id, n), prompting.negative_prompt(lock),
         m.sample_prompts(lock)[n - 1])
        for n in (1, 2, 3)
    ]


def test_the_seed_is_deterministic_per_story_and_sample_so_a_rerun_is_reproducible(store):
    m = _new()
    story_id = _story(store)
    other_id = _story(store)

    seeds = [m.sample_seed(story_id, n) for n in (1, 2, 3)]
    assert seeds == [m.sample_seed(story_id, n) for n in (1, 2, 3)]
    assert len(set(seeds)) == 3
    assert set(seeds).isdisjoint(m.sample_seed(other_id, n) for n in (1, 2, 3))
    assert all(isinstance(s, int) and 1 <= s < 2**31 - 1 for s in seeds)

    first, second = FakeTransport(POLLINATIONS_OK), FakeTransport(POLLINATIONS_OK)
    _preview(store, story_id, FREE, first)
    _preview(store, story_id, FREE, second)
    assert first.urls() == second.urls()


# ============================================================== the route

def test_route_local_never_calls_an_api_link_and_fails_naming_the_route(store, tmp_path):
    story_id = _story(store, route="local")
    transport = FakeTransport(("comfy.test", APIConnectionError("connection refused")),
                              POLLINATIONS_OK, *fal_rules())
    settings = {"IMAGE_CHAIN": "pollinations/flux,local/comfyui,fal/flux-schnell", **FAL, **PAID_ON, **COMFY}

    message, _log = _refused(store, story_id, settings, transport)

    assert "route local" in message
    assert "pollinations/flux: route is local" in message
    assert "fal/flux-schnell: route is local" in message
    assert "local/comfyui: unreachable at http://comfy.test:8188" in message
    # Only the local server was asked whether it is there; no API link was called.
    assert transport.urls() == ["http://comfy.test:8188/system_stats"] * 3
    assert _ledger(store, story_id) == []
    assert not _spend(tmp_path).exists() and not _usage(tmp_path).exists()
    assert list(_folder(store, story_id).iterdir()) == []


def test_route_api_skips_the_local_link(store):
    story_id = _story(store, route="api")
    transport = FakeTransport(POLLINATIONS_OK)
    settings = {"IMAGE_CHAIN": "local/comfyui,pollinations/flux", **COMFY}

    _outcome, log = _preview(store, story_id, settings, transport)

    assert log.count("   ⏭ Skipping local/comfyui: route is api.") == 3
    assert transport.urls("comfy.test") == []
    assert len(transport.urls("pollinations")) == 3


# =============================================================== the money

def test_paid_off_refuses_the_only_paid_link_with_the_numbers_and_sends_nothing(store, tmp_path):
    story_id = _story(store)
    transport = FakeTransport(*gemini_rules())

    message, log = _refused(store, story_id, {"IMAGE_CHAIN": "gemini/nano-banana-2-lite", **GEMINI}, transport)

    # The runner's refusal, verbatim, then the numbers.
    # DEC-223 (AI Story phase 7 stage 2a): the default daily cap is now $6.00 (was $3.00).
    reason = ("gemini/nano-banana-2-lite: paid link; allow_paid is off "
              "(est $0.034 per image; today $0.00 of the $6.00 daily cap)")
    assert reason in message
    assert log.count("   ⏭ Skipping gemini/nano-banana-2-lite: paid link; allow_paid is off.") == 3
    assert transport.calls == []  # RC-P10
    assert _ledger(store, story_id) == []
    assert not _spend(tmp_path).exists()
    doc = store.read_doc(story_id, "style_preview.json")
    assert doc["images"] == [] and doc["failed"] == [{"n": n, "reasons": [reason]} for n in (1, 2, 3)]


def test_paid_on_within_the_caps_sends_one_request_per_sample_and_books_each_image_once(
        store, tmp_path, monkeypatch):
    from clipping.providers import budget

    booked = []
    real_record = budget.record
    monkeypatch.setattr(budget, "record", lambda est, **kw: booked.append(est) or real_record(est, **kw))
    story_id = _story(store)
    transport = FakeTransport(*fal_rules())

    outcome, log = _preview(store, story_id, {"IMAGE_CHAIN": "fal/flux-schnell", **FAL, **PAID_ON}, transport)

    submits = transport.posts("queue.fal.run")
    assert len(submits) == 3  # one attempt per paid sample, never a retry
    assert [json.loads(c["body"])["image_size"] for c in submits] == [{"width": 576, "height": 1024}] * 3
    # Booked once per image: the story ledger and today's spend, nothing else.
    assert booked == [FAL_PER_IMAGE] * 3
    assert list(json.loads(_spend(tmp_path).read_text())["days"].values()) == [0.009]
    assert _rows(_ledger(store, story_id)) == [
        ("style_preview", "fal", "fal-ai/flux/schnell", "image", 1, FAL_PER_IMAGE, True, None)] * 3
    # A paid call never touches the free counters.
    assert not _usage(tmp_path).exists()

    doc = store.read_doc(story_id, "style_preview.json")
    assert [(i["link"], i["paid"], i["est_usd"]) for i in doc["images"]] == [
        ("fal/flux-schnell", True, FAL_PER_IMAGE)] * 3
    assert outcome == {"images": 3, "failed": 0, "est_usd": 0.009}
    assert log.count("   💸 fal/flux-schnell: est $0.003 (paid, allowed)") == 3
    assert log.count("   🔁 fal/flux-schnell: attempt 1/1") == 3
    assert [line for line in log if line.startswith("🖼 preview")] == [
        f"🖼 preview {n}/3 via fal/flux-schnell ($0.003 paid)" for n in (1, 2, 3)]
    assert log[-1] == "🖼 Style preview ready: 3/3 images ($0.009 paid)."


def test_a_cap_that_does_not_fit_is_refused_before_any_request(store, tmp_path):
    story_id = _story(store)
    transport = FakeTransport(*gemini_rules())
    settings = {"IMAGE_CHAIN": "gemini/nano-banana-2-lite", **GEMINI, **PAID_ON, "DAILY_CAP_USD": "0.02"}

    message, _log = _refused(store, story_id, settings, transport)

    assert ("gemini/nano-banana-2-lite: refused: est $0.034 on gemini/nano-banana-2-lite would bring "
            "today to $0.03 of the $0.02 daily cap") in message
    assert transport.calls == []
    assert _ledger(store, story_id) == [] and not _spend(tmp_path).exists()


def test_the_story_cap_reads_the_ledger_as_it_grows(store, tmp_path):
    story_id = _story(store)
    transport = FakeTransport(*gemini_rules())
    settings = {"IMAGE_CHAIN": "gemini/nano-banana-2-lite", **GEMINI, **PAID_ON, "PER_STORY_CAP_USD": "0.07"}

    outcome, _log = _preview(store, story_id, settings, transport)

    assert len(transport.posts()) == 2
    assert outcome == {"images": 2, "failed": 1, "est_usd": 0.0672}
    doc = store.read_doc(story_id, "style_preview.json")
    assert [i["name"] for i in doc["images"]] == ["preview_1.png", "preview_2.png"]
    assert doc["failed"] == [{"n": 3, "reasons": [
        "gemini/nano-banana-2-lite: refused: est $0.034 on gemini/nano-banana-2-lite would bring "
        "this story to $0.10 of its $0.07 cap"]}]
    assert [e["est_usd"] for e in _ledger(store, story_id)] == [GEMINI_PER_IMAGE] * 2
    assert list(json.loads(_spend(tmp_path).read_text())["days"].values()) == [0.0672]


def test_a_ledger_that_cannot_be_read_stops_the_step_before_anything_is_spent(store, tmp_path):
    story_id = _story(store)
    ledger = Path(store.story_dir(story_id)) / "cost_ledger.json"
    ledger.write_text("{not json", encoding="utf-8")
    transport = FakeTransport(*gemini_rules())

    message, _log = _refused(store, story_id, {"IMAGE_CHAIN": "gemini/nano-banana-2-lite", **GEMINI, **PAID_ON},
                             transport)

    assert "cost_ledger.json" in message
    assert transport.calls == []
    assert ledger.read_text(encoding="utf-8") == "{not json"


# ============================================================== failures

def test_a_failing_sample_is_local(store):
    story_id = _story(store)
    transport = FakeTransport(("image.pollinations.ai", sequence(
        (200, PNG), (400, b'{"error": "bad prompt"}'), (200, PNG))))

    outcome, log = _preview(store, story_id, FREE, transport)

    assert outcome == {"images": 2, "failed": 1, "est_usd": 0.0}
    assert sorted(p.name for p in _folder(store, story_id).iterdir()) == ["preview_1.png", "preview_3.png"]
    doc = store.read_doc(story_id, "style_preview.json")
    assert [i["n"] for i in doc["images"]] == [1, 3]
    [failed] = doc["failed"]
    assert failed["n"] == 2 and len(failed["reasons"]) == 1
    assert failed["reasons"][0].startswith("pollinations/flux: HttpStatusError: HTTP 400 from ")
    assert failed["reasons"][0].endswith(": bad prompt")
    assert any(line.startswith("✖ preview 2/3 not made: pollinations/flux: HttpStatusError") for line in log)
    assert len(_ledger(store, story_id)) == 2  # only answered calls are booked


def test_no_image_at_all_fails_the_step_and_records_every_sample(store):
    story_id = _story(store)
    transport = FakeTransport(("image.pollinations.ai", (400, b'{"error": "bad prompt"}')))

    message, _log = _refused(store, story_id, FREE, transport)

    assert message.startswith("No preview image was made on route auto: pollinations/flux: HttpStatusError")
    doc = store.read_doc(story_id, "style_preview.json")
    assert doc["images"] == [] and [f["n"] for f in doc["failed"]] == [1, 2, 3]
    assert _ledger(store, story_id) == []


def test_a_file_that_is_not_an_image_we_keep_fails_its_sample_but_its_call_is_booked(store):
    story_id = _story(store)
    adapter = RecordingAdapter(ext="gif")

    message, _log = _refused(store, story_id, FREE, FakeTransport(),
                             adapters={("image", "pollinations"): adapter})

    assert "pollinations/flux: the answer is a .gif file; only png, jpg, jpeg and webp are kept" in message
    assert list(_folder(store, story_id).iterdir()) == []
    assert _rows(_ledger(store, story_id)) == [
        ("style_preview", "pollinations", "flux", "image", 1, 0.0, False, None)] * 3


def test_a_cancel_between_samples_stops_before_the_next_request(store):
    story_id = _story(store)
    token = CancelToken()
    transport = FakeTransport(("image.pollinations.ai", lambda call: token.cancel() or (200, PNG)))

    with pytest.raises(Cancelled):
        _preview(store, story_id, FREE, transport, token=token)

    assert len(transport.calls) == 1
    assert sorted(p.name for p in _folder(store, story_id).iterdir()) == ["preview_1.png"]
    assert [i["name"] for i in store.read_doc(story_id, "style_preview.json")["images"]] == ["preview_1.png"]
    assert len(_ledger(store, story_id)) == 1


def test_a_story_without_a_style_lock_fails_before_anything(store):
    story_id = _story(store, style=False)
    transport = FakeTransport(POLLINATIONS_OK)

    message, _log = _refused(store, story_id, FREE, transport)

    assert message == "Build the style first."
    assert transport.calls == []
    assert not (Path(store.story_dir(story_id)) / "styles").exists()


def test_a_malformed_image_chain_is_a_step_failure(store):
    story_id = _story(store)

    message, _log = _refused(store, story_id, {"IMAGE_CHAIN": "edge/fr-FR-HenriNeural"}, FakeTransport())

    assert message.startswith("IMAGE_CHAIN cannot be used:")


# ================================================================ the folder

def test_a_new_preview_replaces_the_old_files_and_only_inside_the_preview_folder(store, tmp_path):
    story_id = _story(store)
    story_dir = Path(store.story_dir(story_id))
    folder = story_dir / "styles" / "preview"
    folder.mkdir(parents=True)
    for name in ("preview_1.jpg", "preview_4.webp", "preview_notes.txt", "keep.txt"):
        (folder / name).write_bytes(b"old")
    (folder / "preview_dir").mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"outside")
    (folder / "preview_5.png").symlink_to(outside)
    (story_dir / "preview_9.png").write_bytes(b"story")
    (story_dir / "styles" / "preview_8.png").write_bytes(b"styles")

    _outcome, log = _preview(store, story_id, FREE, FakeTransport(POLLINATIONS_OK))

    assert sorted(p.name for p in folder.iterdir()) == [
        "keep.txt", "preview_1.png", "preview_2.png", "preview_3.png", "preview_dir"]
    assert (folder / "keep.txt").read_bytes() == b"old"
    assert outside.read_bytes() == b"outside"  # the link went, never its target
    assert (story_dir / "preview_9.png").read_bytes() == b"story"
    assert (story_dir / "styles" / "preview_8.png").read_bytes() == b"styles"
    assert "🧹 Removed the previous preview: 4 file(s)." in log


@pytest.mark.parametrize("linked", ["styles", "styles/preview"])
def test_a_symlinked_preview_folder_is_refused_and_nothing_is_written_through_it(store, tmp_path, linked):
    story_id = _story(store)
    story_dir = Path(store.story_dir(story_id))
    elsewhere = tmp_path / "elsewhere"
    (elsewhere / "preview").mkdir(parents=True)
    (elsewhere / "preview" / "preview_1.png").write_bytes(b"theirs")
    (elsewhere / "preview_1.png").write_bytes(b"theirs")
    if linked == "styles":
        (story_dir / "styles").symlink_to(elsewhere)
    else:
        (story_dir / "styles").mkdir()
        (story_dir / "styles" / "preview").symlink_to(elsewhere / "preview")
    transport = FakeTransport(POLLINATIONS_OK)

    message, _log = _refused(store, story_id, FREE, transport)

    assert "styles/preview" in message
    assert transport.calls == []
    assert sorted(p.name for p in elsewhere.rglob("*")) == ["preview", "preview_1.png", "preview_1.png"]
    assert all(p.read_bytes() == b"theirs" for p in elsewhere.rglob("preview_1.png"))


# ============================================================= documents

def _preview_doc(**changes):
    doc = {
        "$schema": "style_preview_v1", "template_id": "fruit_drama", "template_version": 1,
        "overrides": {}, "images": [{"name": "preview_1.png", "n": 1, "link": "pollinations/flux",
                                     "seed": 12, "paid": False, "est_usd": 0.0, "prompt": "p"}],
        "failed": [{"n": 2, "reasons": ["pollinations/flux: HTTP 400"]}], "updated_at": NOW,
    }
    doc.update(changes)
    return doc


def test_the_preview_document_schema():
    assert schemas.STYLE_PREVIEW_SCHEMA_NAME == "style_preview_v1"
    assert schemas.style_preview_errors(_preview_doc()) == []
    image = _preview_doc()["images"][0]
    for name in ("preview_0.png", "preview_1.svg", "../preview_1.png", "preview_10.png", "preview_1.png\n",
                 "story.json"):
        assert schemas.style_preview_errors(_preview_doc(images=[dict(image, name=name)])), name
    assert schemas.style_preview_errors(_preview_doc(extra=1))
    assert schemas.style_preview_errors({k: v for k, v in _preview_doc().items() if k != "failed"})
    assert schemas.style_preview_errors(_preview_doc(images=[dict(image, paid="no")]))


def test_the_preview_document_is_a_story_document(store):
    from clipping.aistory import store as store_mod

    assert "style_preview.json" in store_mod.DOC_NAMES
    story_id = _story(store)
    store.write_doc(story_id, "style_preview.json", _preview_doc(), now=NOW,
                    validator=schemas.style_preview_errors)
    assert store.read_doc(story_id, "style_preview.json")["images"][0]["name"] == "preview_1.png"


# ================================================================ registry

def test_the_preview_step_is_registered_and_runs_through_the_registry(store):
    assert callable(steps.RUNNERS["style_preview"])
    story_id = _story(store, style=False)
    ctx, _log = _ctx(store, story_id, FREE)

    with pytest.raises(steps.StepFailed) as caught:
        steps.run("style_preview", ctx)
    assert str(caught.value) == "Build the style first."


def test_importing_the_registry_imports_no_generation_code():
    code = (
        "import sys, clipping.aistory.steps\n"
        "heavy = ('clipping.aistory.steps.style_preview', 'clipping.providers.generation',\n"
        "         'clipping.providers.gating', 'clipping.providers.images', 'clipping.providers.budget')\n"
        "print(sorted(name for name in heavy if name in sys.modules))\n"
    )
    done = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, check=True)
    assert done.stdout.strip() == "[]"


# ================================================================ estimate

def test_the_estimate_on_a_keyless_machine_names_the_free_link_and_never_a_paid_default(tmp_path):
    m = _new()

    body = m.estimate({}, route="auto")  # the shipped IMAGE_CHAIN, no key at all

    assert body == {
        "step": "style_preview", "est_usd": 0.0, "units": {"images": 3}, "route_class": "free",
        "link": "pollinations/flux", "ready": True, "message": body["message"],
        "links": [
            {"link": "cloudflare/flux-1-schnell", "status": "skipped", "paid": False, "est_usd": 0.0,
             "reason": "no API key (CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are not set)"},
            {"link": "pollinations/flux", "status": "runnable", "reason": "free", "paid": False, "est_usd": 0.0},
            {"link": "local/comfyui", "status": "runnable", "reason": "probed when it runs", "paid": False,
             "est_usd": 0.0},
            {"link": "fal/flux-schnell", "status": "skipped", "reason": "no API key (FAL_KEY is not set)",
             "paid": True, "est_usd": 0.009},
            {"link": "openai/gpt-image-2-low", "status": "skipped",
             "reason": "no API key (OPENAI_API_KEY is not set)", "paid": True, "est_usd": 0.015},
        ],
    }
    assert "pollinations/flux" in body["message"] and "$0.00" in body["message"]
    # An estimate reads; it writes nothing and probes nothing (no_network would fail).
    assert not _usage(tmp_path).exists() and not _spend(tmp_path).exists()


def test_a_paid_estimate_is_three_times_the_links_price():
    m = _new()

    fal = m.estimate({"IMAGE_CHAIN": "fal/flux-schnell,pollinations/flux", **FAL, **PAID_ON}, route="auto")
    gemini = m.estimate({"IMAGE_CHAIN": "gemini/nano-banana-2-lite", **GEMINI, **PAID_ON}, route="auto")

    assert (fal["route_class"], fal["link"], fal["est_usd"], fal["ready"]) == (
        "paid", "fal/flux-schnell", 0.009, True)
    assert fal["links"][0] == {"link": "fal/flux-schnell", "status": "runnable", "reason": "paid, allowed",
                               "paid": True, "est_usd": 0.009}
    assert (gemini["route_class"], gemini["est_usd"]) == ("paid", round(3 * GEMINI_PER_IMAGE, 6))
    assert "$0.101" in gemini["message"]


def test_the_estimate_is_blocked_when_nothing_can_run_and_says_why_per_link():
    m = _new()

    body = m.estimate({"IMAGE_CHAIN": "gemini/nano-banana-2-lite,fal/flux-schnell", **GEMINI, **FAL},
                      route="auto")

    assert (body["route_class"], body["ready"], body["link"], body["est_usd"]) == ("blocked", False, None, 0.0)
    reasons = [row["reason"] for row in body["links"]]
    # DEC-223 (AI Story phase 7 stage 2a): the default daily cap is now $6.00 (was $3.00).
    assert reasons == [
        "refused: est $0.101 on gemini/nano-banana-2-lite; allow_paid is off (today $0.00 of $6.00)",
        "refused: est $0.009 on fal/flux-schnell; allow_paid is off (today $0.00 of $6.00)",
    ]
    for label, reason in zip(("gemini/nano-banana-2-lite", "fal/flux-schnell"), reasons):
        assert f"{label}: {reason}" in body["message"]


def test_the_estimate_follows_the_route():
    m = _new()
    chain = {"IMAGE_CHAIN": "pollinations/flux,local/comfyui", **COMFY}

    local = m.estimate(chain, route="local")
    api = m.estimate({"IMAGE_CHAIN": "local/comfyui,pollinations/flux", **COMFY}, route="api")
    nothing = m.estimate(FREE, route="local")

    assert (local["route_class"], local["link"], local["est_usd"]) == ("local", "local/comfyui", 0.0)
    assert local["links"][0]["reason"] == "route is local"
    assert (api["route_class"], api["link"]) == ("free", "pollinations/flux")
    assert api["links"][0] == {"link": "local/comfyui", "status": "skipped", "reason": "route is api",
                               "paid": False, "est_usd": 0.0}
    assert nothing["route_class"] == "blocked" and "route local" in nothing["message"]
    assert "pollinations/flux: route is local" in nothing["message"]


def test_the_estimate_applies_the_caps_the_story_total_and_a_spent_free_allowance(monkeypatch):
    m = _new()
    daily = m.estimate({"IMAGE_CHAIN": "gemini/nano-banana-2-lite", **GEMINI, **PAID_ON, "DAILY_CAP_USD": "0.05"},
                       route="auto")
    story = m.estimate({"IMAGE_CHAIN": "gemini/nano-banana-2-lite", **GEMINI, **PAID_ON,
                        "PER_STORY_CAP_USD": "0.2"}, route="auto", story_spent=0.15)
    monkeypatch.setenv("LIMIT_CLOUDFLARE_RPD", "0")
    spent = m.estimate({"IMAGE_CHAIN": "cloudflare/flux-1-schnell,pollinations/flux",
                        "CLOUDFLARE_API_TOKEN": "t", "CLOUDFLARE_ACCOUNT_ID": "a"}, route="auto")

    assert daily["route_class"] == "blocked"
    assert daily["links"][0]["reason"] == (
        "refused: est $0.101 on gemini/nano-banana-2-lite would bring today to $0.10 of the $0.05 daily cap")
    assert story["links"][0]["reason"] == (
        "refused: est $0.101 on gemini/nano-banana-2-lite would bring this story to $0.25 of its $0.20 cap")
    assert spent["link"] == "pollinations/flux"
    assert spent["links"][0]["reason"] == "daily allowance spent (0/0 today, resets at 00:00 UTC)"


# ================================================================== the API

@pytest.fixture
def api(monkeypatch, tmp_path):
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import jobs, stories

    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setattr(worker, "_settings_env", dict(FREE))
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(worker, "UPLOADS_ROOT", str(tmp_path / "uploads"))

    submitted = []

    async def fake_submit(job_id, payload):
        submitted.append(job_id)

    monkeypatch.setattr(worker, "submit_job", fake_submit)
    monkeypatch.setenv("DISABLE_AUTH", "1")

    app = FastAPI()
    app.include_router(stories.router)
    app.include_router(jobs.router)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, jobs=job_store, worker=worker, outputs=outputs, submitted=submitted,
            monkeypatch=monkeypatch, store=StoryStore(outputs, on_log=lambda line: None),
        )


def _settings(api, env):
    api.monkeypatch.setattr(api.worker, "_settings_env", dict(env))


def _run_job(api, job_id, transport):
    """Run a queued preview job the way the worker does, its requests answered by *transport*."""
    m = _new()
    job = api.jobs.get_job(job_id)
    api.monkeypatch.setitem(steps.RUNNERS, "style_preview",
                            lambda ctx: m.run(ctx, transport=transport, sleep_fn=_no_sleep))
    api.worker._execute_story_step(job_id, job, CancelToken())


def _post(api, story_id, body=None):
    return api.client.post(f"/api/stories/{story_id}/steps/style_preview", json=body or {})


def test_the_preview_job_awaits_approval_is_served_and_is_completed_by_the_style_approval(api):
    story_id = _story(api.store)

    # No LLM key and no LLM_CHAIN in Settings: a preview needs none. The same
    # Settings refuse an LLM step at the key gate.
    assert api.client.post(f"/api/stories/{story_id}/concepts/generate").status_code == 400
    response = _post(api, story_id)
    assert response.status_code == 201, response.text
    job = response.json()
    assert (job["step"], job["status"], job["story_id"]) == ("style_preview", "queued", story_id)
    assert api.submitted == [job["id"]]

    _run_job(api, job["id"], FakeTransport(POLLINATIONS_OK))

    assert api.jobs.get_job(job["id"])["status"] == "awaiting_approval"
    page = api.client.get(f"/api/stories/{story_id}").json()
    assert [i["name"] for i in page["style_preview"]["images"]] == ["preview_1.png", "preview_2.png",
                                                                     "preview_3.png"]
    assert page["cost_total_usd"] == 0.0
    served = api.client.get(f"/api/stories/{story_id}/files/preview_2.png")
    assert served.status_code == 200 and served.content == PNG
    assert served.headers["content-type"] == "image/png"
    assert served.headers["cache-control"] == "no-store"

    approved = api.client.post(f"/api/stories/{story_id}/approve/style")
    assert approved.status_code == 200, approved.text
    record = api.jobs.get_job(job["id"])
    assert record["status"] == "completed" and record["approved_at"]


def test_the_story_page_has_no_preview_until_one_is_made(api):
    story_id = _story(api.store)
    assert api.client.get(f"/api/stories/{story_id}").json()["style_preview"] is None


def test_a_preview_needs_a_style_lock(api):
    story_id = _story(api.store, style=False)

    response = _post(api, story_id)

    assert response.status_code == 409 and response.json()["detail"] == "Build the style first."
    assert api.jobs.list_jobs() == [] and api.submitted == []


def test_a_preview_nothing_can_make_is_refused_naming_each_link_and_no_job_exists(api):
    story_id = _story(api.store)
    _settings(api, {"IMAGE_CHAIN": "gemini/nano-banana-2-lite,cloudflare/flux-1-schnell", **GEMINI})

    response = _post(api, story_id)

    assert response.status_code == 409
    detail = response.json()["detail"]
    # DEC-223 (AI Story phase 7 stage 2a): the default daily cap is now $6.00 (was $3.00).
    assert ("gemini/nano-banana-2-lite: refused: est $0.101 on gemini/nano-banana-2-lite; allow_paid is off "
            "(today $0.00 of $6.00)") in detail
    assert "cloudflare/flux-1-schnell: no API key" in detail
    assert api.jobs.list_jobs() == [] and api.submitted == []


def test_a_preview_takes_no_parameters(api):
    story_id = _story(api.store)
    response = _post(api, story_id, {"params": {"samples": 9}})
    assert response.status_code == 400 and "no parameters" in response.json()["detail"]
    assert api.jobs.list_jobs() == []


def test_one_preview_at_a_time_a_new_one_supersedes_the_awaiting_one_and_the_queue_cap_holds(api):
    story_id = _story(api.store)
    other_id = _story(api.store)

    first = _post(api, story_id).json()
    busy = _post(api, story_id)
    assert busy.status_code == 409 and first["id"] in busy.json()["detail"]

    api.jobs.set_status(first["id"], api.jobs.JobStatus.AWAITING_APPROVAL)
    second = _post(api, story_id)
    assert second.status_code == 201
    record = api.jobs.get_job(first["id"])
    assert record["status"] == "completed" and record["superseded_by"] == second.json()["id"]

    api.monkeypatch.setenv("MAX_QUEUED_JOBS", "1")
    full = _post(api, other_id)
    assert full.status_code == 429 and "MAX_QUEUED_JOBS=1" in full.json()["detail"]
    assert api.jobs.list_step_jobs(other_id) == []


def test_the_style_cannot_change_or_be_approved_while_its_preview_is_being_made(api):
    story_id = _story(api.store)

    def approve_bible(doc):
        doc["approvals"]["concept"] = NOW
        doc["approvals"]["bible"] = NOW

    api.store.update(story_id, approve_bible, now=NOW)
    job = _post(api, story_id).json()

    for path, body in (("approve/style", None), ("steps/style", {"params": {}})):
        response = api.client.post(f"/api/stories/{story_id}/{path}", json=body)
        assert response.status_code == 409, path
        assert job["id"] in response.json()["detail"]
    assert api.store.read_doc(story_id, "style_lock.json")["locked_at"] is None

    api.jobs.set_status(job["id"], api.jobs.JobStatus.AWAITING_APPROVAL)
    assert api.client.post(f"/api/stories/{story_id}/approve/style").status_code == 200


def test_the_estimate_route_follows_the_story_route_and_its_ledger(api):
    story_id = _story(api.store)
    _settings(api, {"IMAGE_CHAIN": "pollinations/flux,local/comfyui", **COMFY})
    url = f"/api/stories/{story_id}/estimate/style_preview"

    body = api.client.get(url).json()
    assert set(body) == {"step", "est_usd", "units", "route_class", "link", "links", "ready", "message"}
    assert (body["step"], body["units"], body["route_class"], body["link"], body["est_usd"]) == (
        "style_preview", {"images": 3}, "free", "pollinations/flux", 0.0)

    patched = api.client.patch(f"/api/stories/{story_id}", json={"generation_profile": {"route": "local"}})
    assert patched.status_code == 200
    body = api.client.get(url).json()
    assert (body["route_class"], body["link"]) == ("local", "local/comfyui")

    # The story's ledger total counts against its cap.
    CostLedger(str(Path(api.store.story_dir(story_id)) / "cost_ledger.json")).append(
        step="style_preview", provider="gemini", model="x", unit="image", qty=1, est_usd=0.15, paid=True)
    _settings(api, {"IMAGE_CHAIN": "gemini/nano-banana-2-lite", **GEMINI, **PAID_ON, "PER_STORY_CAP_USD": "0.2"})
    api.client.patch(f"/api/stories/{story_id}", json={"generation_profile": {"route": "auto"}})
    body = api.client.get(url).json()
    assert body["route_class"] == "blocked"
    assert "would bring this story to $0.25 of its $0.20 cap" in body["links"][0]["reason"]


def _served(api, story_id, name):
    return api.client.get(f"/api/stories/{story_id}/files/{name}")


@pytest.mark.parametrize("name", [
    "..%2Fx", "%2E%2E%2Fstory.json", "..%2F..%2Fstories.json", "story.json", "style_lock.json",
    "cost_ledger.json", "preview_0.png", "preview_1.svg", "preview_10.png", "preview_1.PNG",
    "preview_1.png%0A", "preview_3.png",
])
def test_files_serves_nothing_but_a_preview_image(api, name):
    story_id = _story(api.store)
    folder = _folder(api.store, story_id)
    folder.mkdir(parents=True)
    (folder / "preview_1.png").write_bytes(PNG)
    (folder / "preview_1.svg").write_bytes(b"<svg/>")
    (folder / "preview_0.png").write_bytes(PNG)
    (folder / "preview_3.png").mkdir()  # a directory under a preview's name

    assert _served(api, story_id, "preview_1.png").status_code == 200
    assert _served(api, story_id, name).status_code == 404


def test_files_never_follows_a_symlink_or_crosses_into_another_story(api, tmp_path):
    story_id = _story(api.store)
    other_id = _story(api.store)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    folder = _folder(api.store, story_id)
    folder.mkdir(parents=True)
    (folder / "preview_1.png").write_bytes(PNG)
    (folder / "preview_2.png").symlink_to(outside)

    assert _served(api, story_id, "preview_1.png").status_code == 200
    assert _served(api, story_id, "preview_2.png").status_code == 404
    assert _served(api, other_id, "preview_1.png").status_code == 404
    assert _served(api, "0123456789ab", "preview_1.png").status_code == 404
    assert _served(api, "../" + story_id, "preview_1.png").status_code == 404

    # A preview folder that is a symlink is not a preview folder.
    linked_id = _story(api.store)
    (tmp_path / "theirs").mkdir()
    (tmp_path / "theirs" / "preview_1.png").write_bytes(PNG)
    (Path(api.store.story_dir(linked_id)) / "styles").mkdir()
    (Path(api.store.story_dir(linked_id)) / "styles" / "preview").symlink_to(tmp_path / "theirs")
    assert _served(api, linked_id, "preview_1.png").status_code == 404
