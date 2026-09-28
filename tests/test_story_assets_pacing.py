"""The ``assets`` step paces a free tier that pushes back instead of failing
what it held back (Tier-2 live finding, 2026-09-28: Pollinations admitted one
image a minute and answered HTTP 402 in between; Gemini TTS answered HTTP 429
after a few lines a minute).

The story, the fakes and the hermetic fixture are the assets step's own
(``tests/test_story_assets_step.py``); the Pollinations and Gemini endpoints
answer through fake transports that read the step's fake clock, and every
pause advances that clock instead of waiting. Offline and hermetic: no key,
chain, cap or limit of the machine reaches a test, no request leaves the
process.

Stdlib + pytest (the CI environment, DEC-012). The step module is imported
inside the tests, so on the parent commit each test fails on its own instead
of the file failing to collect.
"""

from __future__ import annotations

import json
import math

import pytest

import test_story_assets_step as tas
import test_story_episode_steps as eps
import test_story_measure as tsm
from test_story_assets_step import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from clipping.cancel import Cancelled
from clipping.providers import generation, images, tts
from clipping.providers.transport import Response

KIWILO, MANGELLA, BROCCOLIA = tas.KIWILO, tas.MANGELLA, tas.BROCCOLIA
PNG = tas.PNG
MADE = 6  # the shots a test leaves to make: sh01..sh06, six distinct requests
GOOGLE = {"GOOGLE_API_KEY": "test-google-key"}
CLOUDFLARE = {"IMAGE_CHAIN": "cloudflare/flux-1-schnell", "CLOUDFLARE_API_TOKEN": "test-cf-token",
              "CLOUDFLARE_ACCOUNT_ID": "test-cf-account"}
GEMINI_VOICES = {KIWILO: "Kore", MANGELLA: "Puck", BROCCOLIA: "Aoede"}
QUOTA = b'{"error": {"code": 429, "message": "You exceeded your current quota, please check your plan and ' \
        b'billing details.", "status": "RESOURCE_EXHAUSTED"}}'
POLLEN = b'{"error": "Payment Required", "message": "Insufficient pollen balance"}'


def _m():
    from clipping.aistory.steps import assets, episode_common

    return assets, episode_common


# ------------------------------------------------------------------ the fakes

class Clock:
    """The step's fake monotonic clock; its ``sleep`` advances it and records
    every pause."""

    def __init__(self, now=0.0):
        self.now = now
        self.sleeps = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


class Pollinations:
    """``image.pollinations.ai`` on the fake clock: an image when *every*
    seconds have passed since its last one (the first always answers), else
    HTTP 402 -- the empty pollen balance the live free tier answers."""

    def __init__(self, clock, *, every=60.0):
        self.clock = clock
        self.every = every
        self.last = None
        self.answers = []  # the clock at each image
        self.refusals = 0

    def __call__(self, method, url, *, headers=None, body=None, timeout=None):
        assert method == "GET" and url.startswith("https://image.pollinations.ai/prompt/"), url
        now = self.clock()
        if self.last is not None and now - self.last < self.every:
            self.refusals += 1
            return Response(402, {}, POLLEN)
        self.last = now
        self.answers.append(now)
        return Response(200, {"Content-Type": "image/png"}, PNG + str(len(self.answers)).encode())


class GeminiQuota(tsm.GeminiTransport):
    """Gemini's speech endpoint on the fake clock: *per_minute* answers in
    each fake minute (in the minutes of *open_minutes* only, when given),
    then HTTP 429 "You exceeded your current quota" -- the live free tier's
    per-minute quota."""

    def __init__(self, clock, *, per_minute=3, open_minutes=None):
        super().__init__()
        self.clock = clock
        self.per_minute = per_minute
        self.open_minutes = open_minutes
        self.used = {}
        self.refusals = 0

    def __call__(self, method, url, *, headers=None, body=None, timeout=None):
        assert url.startswith("https://generativelanguage.googleapis.com/"), url
        minute = int(self.clock() // 60)
        closed = self.open_minutes is not None and minute not in self.open_minutes
        if closed or self.used.get(minute, 0) >= self.per_minute:
            self.refusals += 1
            return Response(429, {}, QUOTA)
        self.used[minute] = self.used.get(minute, 0) + 1
        return super().__call__(method, url, headers=headers, body=body, timeout=timeout)


class Routes:
    """One transport for the step: each request to the fake its URL names."""

    def __init__(self, *fakes):
        self.fakes = fakes

    def __call__(self, method, url, **kwargs):
        for fake in self.fakes:
            prefix = ("https://image.pollinations.ai/" if isinstance(fake, Pollinations)
                      else "https://generativelanguage.googleapis.com/")
            if url.startswith(prefix):
                return fake(method, url, **kwargs)
        raise AssertionError(f"no fake answers {method} {url}")


class Refusing:
    """Every request answered with *status* (a free link's own refusal)."""

    def __init__(self, status):
        self.status = status
        self.urls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=None):
        self.urls.append(url)
        return Response(self.status, {}, b'{"errors": [{"message": "payment required"}]}')


# ------------------------------------------------------------------ helpers

def _leave_six(store, story_id):
    """Every shot but sh01..sh06 locked, so the step makes six images."""
    board = tas._board(store, story_id)
    for shot in board["shots"][MADE:]:
        shot["assets"]["locked"] = True
    store.write_episode_doc(story_id, 1, "storyboard.json", board, now=tas.NOW)


def _pin_gemini(store, story_id, *who):
    for char_id in who:
        tsm._pin(store, story_id, char_id, "gemini", GEMINI_VOICES[char_id])


def _run(store, story_id, *, adapters, clock, transport=None, settings=None, budget=None, sleep_fn=None,
         ctx=None):
    assets, _ec = _m()
    if ctx is None:
        ctx, log = tas._ctx(store, story_id, settings=settings)
    else:
        log = ctx.on_log
    summary = assets.run(ctx, adapters=adapters, transport=transport, time_fn=clock,
                         sleep_fn=sleep_fn or clock.sleep, budget=budget)
    return summary, log


def _pauses(log):
    return [line for line in log if line.startswith("⏳ ") and " rate-limited: waiting " in line]


def _image_rows(store, story_id):
    return [row for row in tas._ledger(store, story_id) if row["unit"] == "image"]


def _voice_rows(store, story_id):
    return [row for row in tas._ledger(store, story_id) if row["unit"] == "char"]


@pytest.fixture
def quick_retries(monkeypatch):
    """The runner's own 3-second retry of a 429 (a TTS call gets no injected
    sleep) made instant, and Gemini's and Cloudflare's per-minute pacing
    lifted (the allowances still count)."""
    monkeypatch.setattr(generation, "RETRY_BACKOFF_SECONDS", 0.0)
    monkeypatch.setenv("LIMIT_GEMINI_RPM", "0")
    monkeypatch.setenv("LIMIT_CLOUDFLARE_RPM", "0")


# ============================================================ the classification

HTTP_402_POLLINATIONS = "HttpStatusError: HTTP 402 from https://image.pollinations.ai/prompt/a?seed=1: Payment Required"
HTTP_429_GEMINI = ("HttpStatusError: HTTP 429 from https://generativelanguage.googleapis.com/v1beta/models/"
                   "gemini-2.5-flash-lite-preview-tts:generateContent: You exceeded your current quota")
LIVE_CHAIN = [
    ("cloudflare/flux-1-schnell", "no API key (CLOUDFLARE_API_TOKEN and CLOUDFLARE_ACCOUNT_ID are not set)"),
    ("pollinations/flux", HTTP_402_POLLINATIONS),
    ("local/comfyui", "not reachable at http://127.0.0.1:8188"),
    ("fal/flux-schnell", "paid link; allow_paid is off"),
]


@pytest.mark.parametrize("failures, provider", [
    # The live finding: Pollinations' 402 behind a chain of skips.
    (LIVE_CHAIN, "pollinations"),
    ([("pollinations/flux", HTTP_402_POLLINATIONS)], "pollinations"),
    ([("pollinations/flux", "HttpStatusError: HTTP 429 from https://image.pollinations.ai/prompt/a")], "pollinations"),
    ([("gemini/flash-lite-tts", HTTP_429_GEMINI)], "gemini"),
    ([("edge/fr-FR-HenriNeural", "HttpStatusError: HTTP 429 from https://speech.platform.bing.com/")], "edge"),
    ([("cloudflare/flux-1-schnell", "HttpStatusError: HTTP 429 from https://api.cloudflare.com/x")], "cloudflare"),
    ([("pollinations/flux", "RateLimitError: Too many requests")], "pollinations"),
    # A paid link refused at its gates sent nothing: the free one is paced.
    ([("pollinations/flux", HTTP_402_POLLINATIONS),
      ("fal/flux-schnell", "refused: est $0.010 on fal/flux-schnell would bring this episode to $1.01 of its "
                           "$1.00 cap")], "pollinations"),
    ([("pollinations/flux", HTTP_402_POLLINATIONS), ("openai/gpt-image-2-low", "no API key (OPENAI_API_KEY is "
                                                                                 "not set)")], "pollinations"),
    # A 402 anywhere but Pollinations is a refusal, not a rate limit.
    ([("cloudflare/flux-1-schnell", "HttpStatusError: HTTP 402 from https://api.cloudflare.com/x")], None),
    ([("gemini/flash-lite-tts", "HttpStatusError: HTTP 402 from https://generativelanguage.googleapis.com/")],
     None),
    # A paid link is never paced, and a paid request that was sent makes
    # the whole item unpaced (a second one could be billed again).
    ([("fal/flux-schnell", "HttpStatusError: HTTP 429 from https://queue.fal.run/x (paid link: not retried, a "
                           "second request could be billed again)")], None),
    ([("gemini/nano-banana-2-lite", "HttpStatusError: HTTP 429 from https://generativelanguage.googleapis.com/")],
     None),
    ([("pollinations/flux", HTTP_402_POLLINATIONS),
      ("fal/flux-schnell", "RuntimeError: fal/flux-schnell: synthetic failure")], None),
    ([("pollinations/flux", HTTP_402_POLLINATIONS),
      ("fal/flux-schnell", "HttpStatusError: HTTP 500 from https://queue.fal.run/x (request r1 kept for the next "
                           "run; it stays booked)")], None),
    # Everything else: no key, a paid switch, a spent day, a local server,
    # another status, a message that only mentions a 429 further on.
    ([("gemini/flash-lite-tts", "paid link; allow_paid is off")], None),
    ([("cloudflare/flux-1-schnell", "no API key (CLOUDFLARE_API_TOKEN is not set)")], None),
    ([("gemini/flash-lite-tts", "daily allowance spent (250/250 today, resets at 00:00 UTC)")], None),
    ([("local/comfyui", "HttpStatusError: HTTP 429 from http://127.0.0.1:8188/prompt")], None),
    ([("pollinations/flux", "HttpStatusError: HTTP 400 from https://image.pollinations.ai/prompt/a: see HTTP 429")],
     None),
    ([("pollinations/flux", "HttpStatusError: HTTP 503 from https://image.pollinations.ai/prompt/a")], None),
    ([("pollinations/flux", "ProviderError: pollinations/flux: the answer carried no image")], None),
    ([("groq/whisper-large-v3-turbo", "HttpStatusError: HTTP 429 from https://api.groq.com/")], None),
    ([("pollinations", "HttpStatusError: HTTP 402 from https://image.pollinations.ai/")], None),
    ([], None),
    (None, None),
])
def test_rate_limited_by_names_the_free_tier_that_held_an_item_back(failures, provider):
    assets, _ec = _m()
    assert assets.rate_limited_by(failures) == provider


def test_the_pause_is_a_minute():
    assets, _ec = _m()
    assert assets.RATE_LIMIT_PAUSE_S == 60


# ================================================================= the images

def test_pollinations_402s_are_paced_until_every_shot_is_current(store, tmp_path):
    assets, _ec = _m()
    story_id = tas._episode(store, tmp_path)
    _leave_six(store, story_id)
    clock = Clock()
    poll = Pollinations(clock)
    edge = tsm.Edge()

    summary, log = _run(store, story_id, adapters=tas._adapters(edge, image=images.POLLINATIONS), clock=clock,
                        transport=poll)

    shots = tas._shots(store, story_id)
    # One image a minute: the first pass makes sh01, then one pause per
    # shot left, each followed by that shot's image (and the next shot's
    # 402, which ends the round).
    assert poll.answers == [0.0, 60.0, 120.0, 180.0, 240.0, 300.0]
    assert clock.sleeps == [assets.RATE_LIMIT_PAUSE_S] * (MADE - 1) and clock.now == 300.0
    assert poll.refusals == (MADE - 1) + (MADE - 2)
    assert _pauses(log) == [f"⏳ pollinations rate-limited: waiting 60 s before retrying {n} shot{'s' if n > 1 else ''}"
                            for n in range(MADE - 1, 0, -1)]
    for shot in shots[:MADE]:
        assert summary["shots"]["states"][shot["shot_id"]] == "current"
        assert (shot["assets"]["provider"], shot["assets"]["route"], shot["assets"]["est_usd"]) == (
            "pollinations", "free", 0.0)
    assert summary["complete"] is True and summary["failed"] == [] and summary["shots"]["made"] == MADE
    assert log[-1] == "✅ Episode 1's assets are ready for your approval."
    # One ledger row per real answer, none for a refusal; every voice as before.
    assert len(_image_rows(store, story_id)) == MADE
    assert all(row["est_usd"] == 0.0 and row["paid"] is False for row in _image_rows(store, story_id))
    assert summary["lines"]["unvoiced"] == []


def test_a_rerun_after_pacing_makes_no_call(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    _leave_six(store, story_id)
    clock = Clock()
    _run(store, story_id, adapters=tas._adapters(image=images.POLLINATIONS), clock=clock,
         transport=Pollinations(clock))
    rows = len(tas._ledger(store, story_id))
    again = Clock()
    poll = Pollinations(again)

    summary, log = _run(store, story_id, adapters=tas._adapters(image=images.POLLINATIONS), clock=again,
                        transport=poll)

    assert poll.answers == [] and poll.refusals == 0 and again.sleeps == [] and _pauses(log) == []
    assert summary["complete"] is True and len(tas._ledger(store, story_id)) == rows


def test_a_402_from_another_free_link_is_never_paced(store, tmp_path, quick_retries):
    story_id = tas._episode(store, tmp_path)
    _leave_six(store, story_id)
    clock = Clock()
    refusing = Refusing(402)
    adapters = tas._adapters()
    adapters[("image", "cloudflare")] = images.CLOUDFLARE

    summary, log = _run(store, story_id, adapters=adapters, clock=clock, transport=refusing,
                        settings=tas._settings(**CLOUDFLARE))

    # Each shot asked once, failed once: no pause, nothing asked again.
    assert len(refusing.urls) == MADE and clock.sleeps == [] and _pauses(log) == []
    assert [item["target"] for item in summary["failed"]] == [f"shot:1:sh{n:02d}" for n in range(1, MADE + 1)]
    assert all("HTTP 402" in item["reason"] for item in summary["failed"])
    assert summary["complete"] is False and _image_rows(store, story_id) == []


def test_a_shot_whose_paid_link_was_sent_is_never_paced(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    _leave_six(store, story_id)
    clock = Clock()
    poll = Pollinations(clock, every=3600.0)
    fal = tas.FakeImage(price=tas.FAL_PRICE, fail_for={f"shot_{n:02d}" for n in range(1, MADE + 1)})
    settings = tas._settings(IMAGE_CHAIN="pollinations/flux,fal/flux-schnell", **tas.FAL, ALLOW_PAID="1")

    summary, log = _run(store, story_id, adapters=tas._adapters(image=images.POLLINATIONS, fal=fal), clock=clock,
                        transport=poll, settings=settings)

    # sh01 on the free link; every other shot got its 402, then its one paid
    # request, which failed: never asked again, so never bought twice.
    assert poll.answers == [0.0] and poll.refusals == MADE - 1
    assert fal.names() == [f"shot_{n:02d}" for n in range(2, MADE + 1)]
    assert clock.sleeps == [] and _pauses(log) == []
    assert [item["target"] for item in summary["failed"]] == [f"shot:1:sh{n:02d}" for n in range(2, MADE + 1)]


# ================================================================== the voices

def test_gemini_429s_are_paced_until_every_line_is_voiced_on_its_own_voice(store, tmp_path, quick_retries):
    assets, _ec = _m()
    story_id = tas._episode(store, tmp_path)
    _pin_gemini(store, story_id, KIWILO, MANGELLA, BROCCOLIA)
    clock = Clock()
    gemini = GeminiQuota(clock, per_minute=3)
    edge = tsm.Edge()
    image = tas.FakeImage()

    summary, log = _run(store, story_id, adapters=tas._adapters(edge, image=image, gemini=tts.GEMINI_TTS),
                        clock=clock, transport=gemini, settings=tas._settings(**GOOGLE))

    script = eps._script(store, story_id)
    lines = tas._lines(script)
    # Lines with the same words and voice share one answer (the cache).
    distinct = {(GEMINI_VOICES[line["speaker"]], line["text"]) for line in lines}
    rounds = math.ceil(len(distinct) / 3) - 1
    assert len(distinct) > 3 and rounds >= 2
    assert sum(gemini.used.values()) == len(gemini.calls) == len(distinct)
    assert all(count <= 3 for count in gemini.used.values())
    assert clock.sleeps == [assets.RATE_LIMIT_PAUSE_S] * rounds
    assert len(_pauses(log)) == rounds and _pauses(log)[0].startswith("⏳ gemini rate-limited: waiting 60 s before "
                                                                       "retrying ")
    assert _pauses(log)[0].endswith(" lines")
    # Every line voiced by its speaker's pinned voice, and by nothing else.
    for line in lines:
        assert line["timing"]["voice"] == f"gemini/{GEMINI_VOICES[line['speaker']]}", line["line_id"]
        assert line["timing"]["source"] == "audio_duration_only"
    assert {(call["json"]["generationConfig"]["speechConfig"]["voiceConfig"]["prebuiltVoiceConfig"]["voiceName"],
             call["json"]["contents"][0]["parts"][0]["text"]) for call in gemini.calls} == distinct
    assert edge.calls == []
    assert summary["lines"]["unvoiced"] == [] and summary["failed"] == [] and summary["complete"] is True
    assert len(_voice_rows(store, story_id)) == len(distinct)
    assert log[-1] == "✅ Episode 1's assets are ready for your approval."


def test_one_pause_serves_a_rate_limited_voice_and_rate_limited_images(store, tmp_path, quick_retries):
    assets, _ec = _m()
    story_id = tas._episode(store, tmp_path)
    _leave_six(store, story_id)
    _pin_gemini(store, story_id, KIWILO)
    clock = Clock()
    gemini, poll = GeminiQuota(clock, per_minute=1), Pollinations(clock)

    summary, log = _run(store, story_id, adapters=tas._adapters(image=images.POLLINATIONS, gemini=tts.GEMINI_TTS),
                        clock=clock, transport=Routes(gemini, poll), settings=tas._settings(**GOOGLE))

    kiwilo = {line["text"] for line in tas._lines(eps._script(store, story_id)) if line["speaker"] == KIWILO}
    rounds = max(MADE - 1, len(kiwilo) - 1)
    assert clock.sleeps == [assets.RATE_LIMIT_PAUSE_S] * rounds
    assert _pauses(log)[0].startswith("⏳ gemini and pollinations rate-limited: waiting 60 s before retrying ")
    assert _pauses(log)[0].endswith(" lines and 5 shots")
    assert len(poll.answers) == MADE and len(gemini.calls) == len(kiwilo)
    assert summary["complete"] is True and summary["failed"] == []


def test_a_voice_still_refused_after_a_pause_keeps_the_pick_another_voice_advice(store, tmp_path, quick_retries):
    story_id = tas._episode(store, tmp_path)
    _pin_gemini(store, story_id, BROCCOLIA)
    clock = Clock()
    # One answer in the first minute, then the day's quota is gone.
    gemini = GeminiQuota(clock, per_minute=1, open_minutes={0})

    summary, log = _run(store, story_id, adapters=tas._adapters(image=tas.FakeImage(), gemini=tts.GEMINI_TTS),
                        clock=clock, transport=gemini, settings=tas._settings(**GOOGLE))

    hers = [line for line in tas._lines(eps._script(store, story_id)) if line["speaker"] == BROCCOLIA]
    voiced = [line["line_id"] for line in hers if line["timing"]["source"] != "estimated"]
    left = [line["line_id"] for line in hers if line["timing"]["source"] == "estimated"]
    # l17 says l13's words: it shares l13's answer; l28 is refused.
    assert (voiced, left) == (["l13", "l17"], ["l28"]) and len(gemini.calls) == 1
    # One pause, one refused retry (the pass made no progress): given up,
    # and never on another voice.
    assert clock.sleeps == [60] and len(_pauses(log)) == 1
    assert "⏳ gemini is still rate-limited after a 60 s pause: 1 line left as failed." in log
    assert summary["lines"]["unvoiced"] == left
    assert [item["target"] for item in summary["failed"]] == [f"line:1:{line_id}" for line_id in left]
    assert all("HTTP 429" in item["reason"] and item["reason"].startswith("Broccolia: ")
               for item in summary["failed"])
    assert "pick another voice for Broccolia" in log[-1]


def test_a_paid_voice_refused_is_never_paced(store, tmp_path, monkeypatch, quick_retries):
    story_id = tas._episode(store, tmp_path)
    tsm._paid_gemini(monkeypatch)
    _pin_gemini(store, story_id, BROCCOLIA)
    clock = Clock()
    gemini = GeminiQuota(clock)

    summary, log = _run(store, story_id, adapters=tas._adapters(image=tas.FakeImage(), gemini=tts.GEMINI_TTS),
                        clock=clock, transport=gemini, settings=tas._settings(**GOOGLE))

    assert gemini.calls == [] and gemini.refusals == 0 and clock.sleeps == [] and _pauses(log) == []
    assert summary["failed"] and all("allow_paid is off" in item["reason"] for item in summary["failed"])
    assert "pick another voice for Broccolia" in log[-1]


# ======================================================= budget, cancel, stop

def test_a_budget_too_small_for_the_next_pause_stops_and_names_what_is_left(store, tmp_path):
    assets, ec_mod = _m()
    story_id = tas._episode(store, tmp_path)
    _leave_six(store, story_id)
    clock = Clock()
    poll = Pollinations(clock)
    # Room for the first pause and its call (0 + 60 + 300), not the second (60 + 60 + 300).
    budget = ec_mod.Budget(clock, limit=assets.RATE_LIMIT_PAUSE_S + assets.STORY_IMAGE_CALL_SECONDS + 30)

    summary, log = _run(store, story_id, adapters=tas._adapters(image=images.POLLINATIONS), clock=clock,
                        transport=poll, budget=budget)

    assert clock.sleeps == [60] and poll.answers == [0.0, 60.0]  # no second wait was started
    stop = [line for line in log if line.startswith("⏳ Not waiting again")]
    assert stop == ["⏳ Not waiting again: the step's 6-minute budget cannot fit a 60 s pause and the call after it "
                    "(1.0 min used). Left: the images of shots sh03, sh04, sh05 and sh06."]
    # The step ends as a failed shot always did: awaiting the regenerates.
    assert [item["target"] for item in summary["failed"]] == [f"shot:1:sh{n:02d}" for n in range(3, MADE + 1)]
    assert all("HTTP 402" in item["reason"] for item in summary["failed"])
    assert summary["complete"] is False and summary["shots"]["made"] == 2
    assert "regenerate 'shot:1:sh03'" in log[-1] and tas._assets_doc(store, story_id) is not None


def test_a_cancel_during_a_pause_raises_cancelled_and_keeps_what_was_made(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    _leave_six(store, story_id)
    clock = Clock()
    poll = Pollinations(clock)
    ctx, log = tas._ctx(store, story_id)

    def cancelled_while_waiting(seconds):
        clock.sleep(seconds)
        ctx.cancel.cancel()

    with pytest.raises(Cancelled):
        _run(store, story_id, adapters=tas._adapters(image=images.POLLINATIONS), clock=clock, transport=poll,
             sleep_fn=cancelled_while_waiting, ctx=ctx)

    assert clock.sleeps == [60] and poll.answers == [0.0] and poll.refusals == MADE - 1
    assert len(_pauses(log)) == 1
    shots = tas._shots(store, story_id)
    assert shots[0]["assets"]["image"] and not any(shot["assets"]["image"] for shot in shots[1:MADE])
    assert len(_image_rows(store, story_id)) == 1
    assert tas._episode_file(store, story_id, "cost_ledger.json").exists()


def test_a_pass_with_no_progress_stops_the_loop(store, tmp_path):
    story_id = tas._episode(store, tmp_path)
    _leave_six(store, story_id)
    clock = Clock()
    poll = Pollinations(clock, every=3600.0)  # the keyless legacy rate: one image an hour

    summary, log = _run(store, story_id, adapters=tas._adapters(image=images.POLLINATIONS), clock=clock,
                        transport=poll)

    assert clock.sleeps == [60] and poll.answers == [0.0] and poll.refusals == (MADE - 1) + 1
    assert "⏳ pollinations is still rate-limited after a 60 s pause: 5 shots left as failed." in log
    assert [item["target"] for item in summary["failed"]] == [f"shot:1:sh{n:02d}" for n in range(2, MADE + 1)]
    assert summary["complete"] is False and summary["shots"]["made"] == 1
