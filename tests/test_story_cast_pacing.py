"""The ``cast`` step paces a free tier's image and voice-sample limits the
way the ``assets`` step paces its shots and lines (DEC-168, extended here:
T2-P5-F4, a live finding 2026-09-30 -- an accepted proposed character needed
six separate cast presses a minute apart because pollinations answered HTTP
402 except for about one image a minute).

The story and the fakes are the cast step's own (``tests/
test_story_cast_steps.py``); pollinations and gemini answer through fake
adapters that read the step's fake clock, and every pause advances that
clock instead of waiting (DEC-176: no test here waits on wall-clock time).
Offline and hermetic (``test_story_cast_steps.hermetic``, autouse): no key,
chain, cap or limit of the machine reaches a test, no request leaves the
process.

Stdlib + pytest (the CI environment, DEC-012). The step module is imported
inside the tests, so on the parent commit each test fails on its own instead
of the file failing to collect.
"""

from __future__ import annotations

import pytest

import test_story_cast_steps as tcs
from test_story_cast_steps import hermetic, store  # noqa: F401 - the step's fixtures (hermetic is autouse)
from clipping.aistory import refimages
from clipping.aistory.steps import entities as entities_mod
from clipping.aistory.steps import episode_common
from clipping.aistory.steps.llm_call import StepFailed
from clipping.cancel import CancelToken
from clipping.providers import generation, tts
from clipping.providers.transport import HttpStatusError

FakeImage, FakeTTS, FakeLLM, Log = tcs.FakeImage, tcs.FakeTTS, tcs.FakeLLM, tcs.Log
K1_KIWI, K1_MANGO = tcs.K1_KIWI, tcs.K1_MANGO
NOW = tcs.NOW


def _m():
    from clipping.aistory.steps import cast

    return cast


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


class PacedImage(FakeImage):
    """A free image link on the fake clock: an image when *every* seconds
    have passed since its last one (the first always answers), else the
    given HTTP status -- pollinations' empty pollen balance (402), refilled
    about one image a minute."""

    def __init__(self, clock, *, every=60.0, status=402, url="https://image.pollinations.ai/prompt/x",
                 detail="Insufficient pollen balance"):
        super().__init__()
        self.clock = clock
        self.every = every
        self.last = None
        self.status, self.url, self.detail = status, url, detail

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        now = self.clock()
        if self.last is not None and now - self.last < self.every:
            raise HttpStatusError(self.status, self.url, self.detail)
        self.last = now
        return super().generate(link, request, credentials=credentials, on_log=on_log, transport=transport)


class AlwaysRefuse(FakeImage):
    """A free image link that always refuses with the given HTTP status --
    no clock, no throttle."""

    def __init__(self, *, status=402, url="https://image.pollinations.ai/prompt/x",
                 detail="Insufficient pollen balance"):
        super().__init__()
        self.status, self.url, self.detail = status, url, detail

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        raise HttpStatusError(self.status, self.url, self.detail)


class AlwaysBillable(FakeImage):
    """A paid link whose request is sent and fails for another reason -- the
    item must never be asked again (DEC-106)."""

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        raise RuntimeError(f"{link.provider}/{link.model}: synthetic failure")


@pytest.fixture
def quick_retries(monkeypatch):
    """The runner's own retry-on-429 made instant. ``voices.synthesize_sample``
    hands the chain runner no clock of its own (it always uses real
    ``time.sleep``), so this only keeps its retry backoff from really
    waiting (DEC-176); the pacing pause itself is always the step's own fake
    clock, never this."""
    monkeypatch.setattr(generation, "RETRY_BACKOFF_SECONDS", 0.0)


# ------------------------------------------------------------------ helpers

PROMPT_ONLY_TWO = {"selected": ["Kiwilo", "Mangella"]}
CUSTOM_ZESTY = {"custom": [{"name": "Zesty", "role": "support", "one_line": "Un citron zesté qui complote."}]}
K1_ZESTY = tcs.k1("an anthropomorphic lemon with a spiky rind", ["a monocle", "a tiny bow tie"])
CLOUDFLARE_SETTINGS = dict(tcs.SETTINGS, IMAGE_CHAIN="cloudflare/flux-1-schnell",
                           CLOUDFLARE_API_TOKEN="test-cf-token", CLOUDFLARE_ACCOUNT_ID="test-cf-account")
FAL_SETTINGS = dict(tcs.SETTINGS, IMAGE_CHAIN="pollinations/flux,fal/flux-schnell", FAL_KEY="test-fal-key",
                    ALLOW_PAID="1")


def _season_doc(episodes=3):
    return {
        "$schema": "season_arc_v1",
        "episodes_planned": episodes,
        "arc": [{"ep": n, "function": "setup", "summary": "x", "open_hooks_in": [], "open_hooks_out": [],
                "characters": []} for n in range(1, episodes + 1)],
        "series_memory": {"recaps": {}, "open_hooks": [], "relationship_state": {}, "introduced": {}},
        "audience_feedback": [],
        "approved_at": None,
        "updated_at": NOW,
    }


def _run(store, story_id, *, adapters, clock, params=tcs.CAST_PARAMS, llm=None, settings=None, budget=None,
         sleep_fn=None, transport=None):
    cast = _m()
    ctx, log = tcs._ctx(store, story_id, step="cast", params=params, settings=settings)
    llm = tcs._cast_llm() if llm is None else llm
    summary = cast.run(ctx, runner=llm, time_fn=clock, sleep_fn=sleep_fn or clock.sleep, adapters=adapters,
                       transport=transport, budget=budget)
    return summary, log, llm


def _failed(store, story_id, *, adapters, clock, params=tcs.CAST_PARAMS, llm=None, settings=None, budget=None,
            sleep_fn=None, transport=None):
    cast = _m()
    ctx, log = tcs._ctx(store, story_id, step="cast", params=params, settings=settings)
    llm = tcs._cast_llm() if llm is None else llm
    with pytest.raises(StepFailed) as caught:
        cast.run(ctx, runner=llm, time_fn=clock, sleep_fn=sleep_fn or clock.sleep, adapters=adapters,
                 transport=transport, budget=budget)
    return str(caught.value), log


def _pauses(log):
    return [line for line in log if line.startswith("⏳ ") and " rate-limited: waiting " in line]


# ================================================================= the images

def test_pollinations_402s_are_paced_until_every_sheet_exists(store):
    story_id = tcs._story(store, mode="prompt_only")
    clock = Clock()
    poll = PacedImage(clock, every=60.0)
    adapters = {("image", "pollinations"): poll, ("tts", "gemini"): FakeTTS()}
    llm = FakeLLM(K1=[K1_KIWI, K1_MANGO])

    summary, log, _llm = _run(store, story_id, adapters=adapters, clock=clock, params=PROMPT_ONLY_TWO, llm=llm)

    # 2 characters x 3 images: the first (Kiwilo's portrait) free, the other
    # 5 each its own round (one image a minute).
    assert clock.sleeps == [60.0] * 5 and clock.now == 300.0
    assert _pauses(log) == [
        "⏳ pollinations rate-limited: waiting 60 s before retrying 3 images",
        "⏳ pollinations rate-limited: waiting 60 s before retrying 2 images",
        "⏳ pollinations rate-limited: waiting 60 s before retrying 1 image",
        "⏳ pollinations rate-limited: waiting 60 s before retrying 2 images",
        "⏳ pollinations rate-limited: waiting 60 s before retrying 1 image",
    ]
    assert summary["images"] == {
        "char_kiwilo": {"portrait": "base", "turnaround": "prompt_only", "expressions": "prompt_only"},
        "char_mangella": {"portrait": "base", "turnaround": "prompt_only", "expressions": "prompt_only"},
    }
    assert summary["needs_editor"] == []
    assert summary["samples"] == {"char_kiwilo": "voice_sample.mp3", "char_mangella": "voice_sample.mp3"}


def test_a_rerun_after_pacing_makes_no_call(store):
    story_id = tcs._story(store, mode="prompt_only")
    clock = Clock()
    poll = PacedImage(clock, every=60.0)
    adapters = {("image", "pollinations"): poll, ("tts", "gemini"): FakeTTS()}
    _run(store, story_id, adapters=adapters, clock=clock, params=PROMPT_ONLY_TWO,
        llm=FakeLLM(K1=[K1_KIWI, K1_MANGO]))

    again = Clock()
    poll2 = PacedImage(again, every=60.0)
    summary, log, llm = _run(store, story_id, adapters={("image", "pollinations"): poll2,
                                                         ("tts", "gemini"): FakeTTS()},
                             clock=again, params={}, llm=FakeLLM())

    assert llm.calls == [] and poll2.calls == 0 and again.sleeps == [] and _pauses(log) == []
    assert summary == {"created": [], "written": [], "images": {}, "needs_editor": [], "voices": {},
                       "pick_voice": [], "samples": {}}


def test_the_accepted_proposal_path_completes_in_one_run_under_402s(store):
    story_id = tcs._story(store, mode="prompt_only")
    store.write_doc(story_id, "season.json", _season_doc(3), now=NOW)
    clock = Clock()
    poll = PacedImage(clock, every=60.0)
    adapters = {("image", "pollinations"): poll, ("tts", "gemini"): FakeTTS()}
    params = dict(CUSTOM_ZESTY, introduced_in=2)

    summary, log, _llm = _run(store, story_id, adapters=adapters, clock=clock, params=params,
                              llm=FakeLLM(K1=[K1_ZESTY]))

    assert clock.sleeps == [60.0, 60.0]
    assert summary["created"] == ["char_zesty"]
    assert summary["images"] == {"char_zesty": {"portrait": "base", "turnaround": "prompt_only",
                                                "expressions": "prompt_only"}}
    assert summary["samples"] == {"char_zesty": "voice_sample.mp3"}
    season = store.read_doc(story_id, "season.json")
    assert season["series_memory"]["introduced"] == {"ep02": ["char_zesty"]}


def test_a_402_from_another_free_link_is_never_paced(store):
    story_id = tcs._story(store, mode="prompt_only")
    clock = Clock()
    refusing = AlwaysRefuse(status=402, url="https://api.cloudflare.com/client/v4/x", detail="payment required")
    adapters = {("image", "cloudflare"): refusing, ("tts", "gemini"): FakeTTS()}

    message, log = _failed(store, story_id, adapters=adapters, clock=clock, params=tcs.CAST_PARAMS,
                           settings=CLOUDFLARE_SETTINGS)

    # Each portrait asked once, failed once: no pause, nothing asked again.
    assert clock.sleeps == [] and _pauses(log) == []
    assert message.startswith("Cast incomplete:")
    not_made = [line for line in log if "not made" in line]
    assert len(not_made) == 3 and all("cloudflare/flux-1-schnell" in line and "HTTP 402" in line
                                      for line in not_made)


def test_a_paid_sent_item_is_never_paced(store):
    story_id = tcs._story(store, mode="prompt_only")
    clock = Clock()
    poll = AlwaysRefuse(status=402, detail="Insufficient pollen balance")
    fal = AlwaysBillable()
    adapters = {("image", "pollinations"): poll, ("image", "fal"): fal, ("tts", "gemini"): FakeTTS()}

    message, log = _failed(store, story_id, adapters=adapters, clock=clock, params=tcs.CAST_PARAMS,
                           settings=FAL_SETTINGS)

    # Every portrait got its 402, then its one paid request, which failed:
    # never asked again, so never paced and never bought twice.
    assert clock.sleeps == [] and _pauses(log) == []
    not_made = [line for line in log if "not made" in line]
    assert len(not_made) == 3
    assert all("HTTP 402" in line and "fal/flux-schnell: RuntimeError: fal/flux-schnell: synthetic failure" in line
              for line in not_made)
    assert message.startswith("Cast incomplete:")


# ======================================================= budget, stop, no progress

def test_a_budget_too_small_for_the_next_pause_stops_and_names_what_is_left(store):
    cast = _m()
    story_id = tcs._story(store, mode="prompt_only")
    clock = Clock()
    poll = PacedImage(clock, every=60.0)
    adapters = {("image", "pollinations"): poll, ("tts", "gemini"): FakeTTS()}
    # Room for the first pause and its call (0 + 60 + 300), not the second.
    budget = episode_common.Budget(clock, limit=cast.pacing.RATE_LIMIT_PAUSE_S + cast._CAST_IMAGE_CALL_SECONDS + 30)

    message, log = _failed(store, story_id, adapters=adapters, clock=clock, params=PROMPT_ONLY_TWO,
                           llm=FakeLLM(K1=[K1_KIWI, K1_MANGO]), budget=budget)

    assert clock.sleeps == [60.0]  # no second wait was started
    stop = [line for line in log if line.startswith("⏳ Not waiting again")]
    assert stop == ["⏳ Not waiting again: the step's 6-minute budget cannot fit a 60 s pause and the call after "
                    "it (1.0 min used). Left: the expressions of Kiwilo; the portrait of Mangella."]
    assert "regenerate" in message


def test_a_round_with_no_progress_stops(store):
    story_id = tcs._story(store, mode="prompt_only")
    clock = Clock()
    poll = PacedImage(clock, every=3600.0)  # the keyless legacy rate: one image an hour
    adapters = {("image", "pollinations"): poll, ("tts", "gemini"): FakeTTS()}

    message, log = _failed(store, story_id, adapters=adapters, clock=clock, params=PROMPT_ONLY_TWO,
                           llm=FakeLLM(K1=[K1_KIWI, K1_MANGO]))

    assert clock.sleeps == [60.0]
    assert "⏳ pollinations is still rate-limited after a 60 s pause: 3 images left as failed." in log
    assert "Cast incomplete:" in message


# ================================================================== the voices

def test_a_429_on_the_voice_sample_is_paced(store, quick_retries):
    from test_story_assets_pacing import GeminiQuota

    story_id = tcs._with_cast(store)  # a normal cast: 3 characters, Gemini voices and samples made
    doc = store.read_entity(story_id, "characters", "char_kiwilo")
    doc["voice"] = dict(doc["voice"], provider="gemini", voice_id="Kore")
    store.write_entity(story_id, "characters", doc, now=NOW)
    entities_mod.drop_sample(store, story_id, "char_kiwilo")
    clock = Clock()
    gemini = GeminiQuota(clock, per_minute=1, open_minutes={1})
    adapters = {("image", "pollinations"): FakeImage(), ("tts", "gemini"): tts.GEMINI_TTS}
    settings = dict(tcs.SETTINGS, GOOGLE_API_KEY="test-google-key")

    summary, log, _llm = _run(store, story_id, adapters=adapters, clock=clock, params={}, llm=FakeLLM(),
                              settings=settings, transport=gemini)

    assert clock.sleeps == [60.0]
    assert _pauses(log) == ["⏳ gemini rate-limited: waiting 60 s before retrying 1 voice sample"]
    assert summary["samples"] == {"char_kiwilo": "voice_sample.wav"}
    assert store.media_path(story_id, "characters", "char_kiwilo", "voice_sample.wav")


# ================================================================ NeedsEditor

def test_needs_editor_still_stops_and_asks_without_a_pause(store):
    story_id = tcs._story(store)  # references mode
    clock = Clock()
    poll = PacedImage(clock, every=60.0)
    adapters = {("image", "pollinations"): poll, ("tts", "gemini"): FakeTTS()}

    summary, log, _llm = _run(store, story_id, adapters=adapters, clock=clock, params=tcs.CAST_PARAMS,
                              settings=tcs.NO_EDITOR)

    # Kiwilo's portrait on the free link needs no pause; Mangella's and
    # Figuette's each do -- and once made, their sheets hit NeedsEditor at
    # once, never adding a pause of their own.
    assert clock.sleeps == [60.0, 60.0]
    assert _pauses(log) == [
        "⏳ pollinations rate-limited: waiting 60 s before retrying 2 images",
        "⏳ pollinations rate-limited: waiting 60 s before retrying 1 image",
    ]
    assert [item["char_id"] for item in summary["needs_editor"]] == ["char_kiwilo", "char_mangella",
                                                                      "char_figuette"]
    assert summary["images"] == {cid: {"portrait": "base"} for cid in tcs.IDS}


# ============================================================ the raw failures

def test_the_raw_failures_survive_noimage_into_refimageerror(store):
    story_id = tcs._with_cast(store)
    adapters = {("image", "pollinations"): AlwaysRefuse()}
    log = Log()

    with pytest.raises(refimages.RefImageError) as caught:
        refimages.character_image(store, story_id, "char_kiwilo", "portrait", env=dict(tcs.SETTINGS), on_log=log,
                                  cancel=CancelToken(), adapters=adapters, sleep_fn=lambda seconds: None)

    exc = caught.value
    assert exc.failures == (
        ("pollinations/flux",
         "HttpStatusError: HTTP 402 from https://image.pollinations.ai/prompt/x: Insufficient pollen balance"),
    )
    cast = _m()
    assert cast.pacing.rate_limited_by(exc.failures) == "pollinations"
