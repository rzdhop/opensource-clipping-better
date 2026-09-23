"""A cancelled job stops asking for more work.

The web worker runs a job in a thread, and a thread cannot be killed from
outside, so DELETE /api/jobs/{id} used to flip the status to "cancelled" while
the pipeline carried on: every remaining provider request, transcription chunk
and render was still made, and paid for in free-tier quota and CPU.

clipping.cancel gives the pipeline a token it checks before each step that
starts spending: a chain link, an attempt, a schema re-ask, a backoff, a probe,
an STT chunk, a Whisper segment. The CLI never has a token, so for it nothing
changes -- and its calls do not even gain the keyword.

Everything here is stdlib or faked, so it runs in the pytest-only CI job.
"""

import json
import sys
import threading
import time
import types
from types import SimpleNamespace

import pytest

from clipping import cancel as cancel_mod
from clipping.cancel import Cancelled, CancelToken
from clipping.providers import llm, pacing, stt
from clipping.providers.registry import Link, parse_chain

GOOD = json.dumps([{"a": 1}])


# -------------------------------------------------------------------- token

def test_a_cancelled_token_raises_on_check():
    token = CancelToken()
    token.check()
    token.cancel()
    assert token.cancelled
    with pytest.raises(Cancelled):
        token.check()


def test_cancelled_escapes_except_exception():
    """The pipeline's many `except Exception` blocks record a failure and try
    the next provider; a cancel caught by one would be retried as a failure."""
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        try:
            token.check()
        except Exception:  # noqa: BLE001 - the point of the test
            pytest.fail("Cancelled was caught by `except Exception`")


def test_sleep_is_cut_short_by_a_cancel():
    token = CancelToken()
    threading.Timer(0.05, token.cancel).start()
    started = time.monotonic()
    with pytest.raises(Cancelled):
        token.sleep(5)
    assert time.monotonic() - started < 2


def test_an_injected_sleep_is_bracketed_by_checks():
    token = CancelToken()
    slept = []

    def fake_sleep(seconds):
        slept.append(seconds)
        token.cancel()

    with pytest.raises(Cancelled):
        token.sleeper(fake_sleep)(3)
    assert slept == [3]


def test_a_cfg_without_a_token_is_never_cancelled():
    token = cancel_mod.token_of(SimpleNamespace())
    token.check()
    assert token is cancel_mod.NEVER
    with pytest.raises(RuntimeError):
        cancel_mod.NEVER.cancel()


def test_a_cfg_token_is_returned():
    token = CancelToken()
    assert cancel_mod.token_of(SimpleNamespace(cancel_token=token)) is token


def test_the_keyword_is_only_passed_for_a_real_token():
    assert cancel_mod.kwargs_for(cancel_mod.NEVER) == {}
    token = CancelToken()
    assert cancel_mod.kwargs_for(token) == {"cancel": token}


# -------------------------------------------------------------- the LLM chain

class FakeCompletions:
    def __init__(self, owner):
        self._owner = owner

    def create(self, **kwargs):
        self._owner.calls.append(kwargs)
        item = self._owner.scripted.pop(0)
        if callable(item):
            item = item()
        if isinstance(item, BaseException):
            raise item
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=item))],
            usage=SimpleNamespace(total_tokens=10),
        )


class FakeClient:
    def __init__(self, scripted):
        self.scripted = list(scripted)
        self.calls = []
        self.chat = SimpleNamespace(completions=FakeCompletions(self))


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    llm.reset_negotiation()
    pacing.reset_limiters()
    monkeypatch.setattr(pacing, "limiter_for",
                        lambda provider: pacing.Limiter("t", rpm=None, tpm=None))
    yield
    llm.reset_negotiation()
    pacing.reset_limiters()


def transient():
    err = type("APIConnectionError", (Exception,), {})("connection reset")
    return err


def test_a_cancelled_chain_makes_no_request_and_announces_no_attempt():
    fake = FakeClient([GOOD])
    token = CancelToken()
    token.cancel()
    lines = []

    with pytest.raises(Cancelled):
        llm.run_chain(parse_chain("groq/m"), system="s", user="u",
                      keys={"groq": "k"}, on_log=lines.append,
                      client_factory=lambda *a, **k: fake, cancel=token)

    assert fake.calls == []
    assert not any("attempt" in line for line in lines)


def test_a_cancel_during_backoff_stops_the_ladder_and_the_chain():
    """Neither the second attempt nor the next link is tried, and the cancel is
    not reported as that link failing."""
    first = FakeClient([transient()])
    second = FakeClient([GOOD])
    clients = iter([first, second])
    token = CancelToken()
    lines = []

    def sleep_then_cancel(seconds):
        token.cancel()

    with pytest.raises(Cancelled):
        llm.run_chain(parse_chain("groq/m,gemini/m"), system="s", user="u",
                      keys={"groq": "k", "gemini": "k"}, on_log=lines.append,
                      client_factory=lambda *a, **k: next(clients),
                      sleep_fn=sleep_then_cancel, cancel=token)

    assert len(first.calls) == 1
    assert second.calls == []
    assert not any("failed |" in line and "Cancelled" in line for line in lines)


def test_a_schema_re_ask_is_not_sent_after_a_cancel():
    token = CancelToken()
    rejected = type("BadRequestError", (Exception,), {})("response_format json_schema is not supported")
    rejected.status_code = 400

    def reject_and_cancel():
        token.cancel()
        return rejected

    fake = FakeClient([reject_and_cancel, GOOD])
    client = llm.LlmClient(Link("groq", "m"), api_key="k",
                           client_factory=lambda *a, **k: fake,
                           limiter=pacing.Limiter("t", rpm=None, tpm=None))

    with pytest.raises(Cancelled):
        client.complete_json(system="s", user="u",
                             schema={"type": "array"}, cancel=token)
    assert len(fake.calls) == 1


def test_a_cancelled_probe_pings_nothing():
    fake = FakeClient(["ok"])
    token = CancelToken()
    token.cancel()
    with pytest.raises(Cancelled):
        llm.probe_chain(parse_chain("groq/m"), {"groq": "k"},
                        on_log=lambda *a: None,
                        client_factory=lambda *a, **k: fake, cancel=token)
    assert fake.calls == []


def test_without_a_token_the_chain_behaves_as_before():
    fake = FakeClient([GOOD])
    value, link = llm.run_chain(parse_chain("groq/m"), system="s", user="u",
                                keys={"groq": "k"}, on_log=lambda *a: None,
                                client_factory=lambda *a, **k: fake)
    assert value == [{"a": 1}] and link.provider == "groq"


# ------------------------------------------------------------ transcription

def test_a_cancel_stops_hosted_transcription_between_chunks(monkeypatch, tmp_path):
    audio = tmp_path / "a.flac"
    audio.write_bytes(b"\x00" * 16)
    monkeypatch.setattr(stt.audio_mod, "extract", lambda *a, **k: str(audio))
    monkeypatch.setattr(stt.audio_mod, "plan_chunks", lambda *a, **k: [(0.0, 60.0), (60.0, 120.0)])
    monkeypatch.setattr(stt.audio_mod, "cut", lambda path, s, e: str(audio))
    token = CancelToken()
    sent = []

    def post(url, key, file_path, fields):
        sent.append(fields)
        token.cancel()
        return {"language": "en", "text": "hi",
                "words": [{"word": "hi", "start": 0.0, "end": 0.5}]}

    with pytest.raises(Cancelled):
        stt.transcribe("v.mp4", chain=[Link("groq", "whisper-large-v3-turbo")],
                       keys={"groq": "k"}, on_log=lambda *a: None, post=post,
                       cancel=token)
    assert len(sent) == 1


def test_a_cancel_stops_local_whisper_between_segments(monkeypatch):
    from clipping import engine

    fake_tqdm = types.ModuleType("tqdm")

    class Bar:
        n = 0

        def __init__(self, *a, **k):
            pass

        def update(self, n):
            self.n += n

        def close(self):
            pass

    fake_tqdm.tqdm = Bar
    monkeypatch.setitem(sys.modules, "tqdm", fake_tqdm)
    monkeypatch.setattr(engine, "_warn_if_cpu_transcription_will_be_slow",
                        lambda *a, **k: None, raising=False)

    token = CancelToken()
    produced = []

    def segments():
        for i in range(5):
            produced.append(i)
            if i == 1:
                token.cancel()
            yield SimpleNamespace(start=float(i), end=float(i) + 1, text=" hi",
                                  words=[SimpleNamespace(word="hi", start=float(i), end=float(i) + 0.5)])

    model = SimpleNamespace(transcribe=lambda *a, **k: (segments(), SimpleNamespace(duration=5.0, language="en")))

    with pytest.raises(Cancelled):
        engine.transcribe_video("v.mp4", model=model, cancel=token)
    assert produced == [0, 1]


# ------------------------------------------------- the cfg token, wired through

def _segmen(n_sentences=60):
    out, t = [], 0.0
    for i in range(n_sentences):
        chunk = [{"word": w, "start": t + j * 0.4, "end": t + (j + 1) * 0.4 - 0.01}
                 for j, w in enumerate(f"sentence number {i} says something here.".split())]
        out.append({"start": chunk[0]["start"], "end": chunk[-1]["end"], "words": chunk})
        t = chunk[-1]["end"] + 0.8
    return out


def _analysis_cfg(**extra):
    return SimpleNamespace(jumlah_clip=3, platform="tiktok", durasi_hook=3,
                           use_broll=False, pexels_api_key="", hook_v2=False,
                           no_segment_trim=False, output_language="en",
                           analysis_budget_seconds=900, analysis_workers=1, **extra)


def test_a_cancelled_analysis_makes_no_further_request():
    from clipping.analysis import analyzer

    token = CancelToken()
    calls = []

    def runner(chain, **kwargs):
        calls.append(kwargs)
        token.cancel()  # the user cancels while the first window is answered
        return {"candidates": []}, chain[0]

    with pytest.raises(Cancelled):
        analyzer.analyze(_segmen(), _analysis_cfg(cancel_token=token),
                         chain=[("groq", "m")], keys={"groq": "k"},
                         on_log=lambda *a: None, run_chain=runner)
    assert len(calls) == 1
    assert calls[0]["cancel"] is token


def test_an_analysis_without_a_token_passes_no_cancel_keyword():
    from clipping.analysis import analyzer

    calls = []

    def runner(chain, **kwargs):
        calls.append(kwargs)
        raise RuntimeError("stop after the first request")

    with pytest.raises(Exception):
        analyzer.analyze(_segmen(), _analysis_cfg(), chain=[("groq", "m")],
                         keys={"groq": "k"}, on_log=lambda *a: None, run_chain=runner)
    assert calls and all("cancel" not in kwargs for kwargs in calls)


def test_resolve_transcript_hands_the_token_to_hosted_stt(monkeypatch):
    from clipping import runner as runner_mod
    from clipping.providers import stt as stt_mod

    token = CancelToken()
    seen = {}

    def fake_transcribe(video, **kwargs):
        seen.update(kwargs)
        return "t", [], "en"

    monkeypatch.setattr(stt_mod, "transcribe", fake_transcribe)
    monkeypatch.setattr("clipping.config.provider_keys", lambda cfg: {"groq": "k"})
    cfg = SimpleNamespace(stt_chain="groq/whisper-large-v3-turbo", file_video_asli="v.mp4",
                          max_kata_per_subtitle=5, output_language="auto",
                          detected_language="", cancel_token=token)

    runner_mod._transcribe(cfg)
    assert seen.get("cancel") is token


def test_resolve_transcript_hands_the_token_to_local_whisper(monkeypatch):
    from clipping import engine
    from clipping import runner as runner_mod

    token = CancelToken()
    seen = {}

    def fake_whisper(video, **kwargs):
        seen.update(kwargs)
        return "t", []

    monkeypatch.setattr(engine, "transcribe_video", fake_whisper)
    monkeypatch.setattr("clipping.config.provider_keys", lambda cfg: {})
    cfg = SimpleNamespace(stt_chain="local", file_video_asli="v.mp4",
                          max_kata_per_subtitle=5, whisper_model="tiny",
                          whisper_device="cpu", whisper_compute_type="int8",
                          cancel_token=token)

    runner_mod._transcribe(cfg)
    assert seen.get("cancel") is token
