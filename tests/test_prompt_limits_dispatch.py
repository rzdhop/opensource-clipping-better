"""The chain runner refuses a prompt over its link's limit
(``prompt_limits``) before anything is called -- never truncated, never sent
-- and moves on to the next link as with any other refusal: no estimate, no
budget verdict, no free-tier slot, no journal entry, and the story side
reads it as nothing sent and the link not gone. Stdlib + pytest.
"""

from __future__ import annotations

import pytest

from clipping.providers import generation, prompt_limits
from clipping.providers.generation import GenRequest, GenResult, NoRunnableLink, parse_generation_chain
from clipping.providers.registry import Link

KLING = Link("fal", "kling-2.5-turbo-std")


@pytest.fixture(autouse=True)
def _isolated_live_file(tmp_path, monkeypatch):
    """Table values only: the live file follows the Settings file's folder."""
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))


class Adapter:
    def __init__(self, est=None):
        self.calls = []
        self.est = est

    def estimate(self, link, request):
        return self.est

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.calls.append(link)
        return GenResult(provider=link.provider, model=link.model, paths=("out",), seed=7)


class Limiter:
    def __init__(self):
        self.asked = []

    def acquire(self, provider):
        self.asked.append(provider)


ENV = {"FAL_KEY": "f", "GOOGLE_API_KEY": "g", "GEMINI_PAID_API_KEY": "g", "CLOUDFLARE_API_TOKEN": "c",
       "CLOUDFLARE_ACCOUNT_ID": "a"}


def _run(kind, chain, request, adapters, **kw):
    log = []
    kw.setdefault("allow_paid", True)
    result = generation.run_generation_chain(kind, parse_generation_chain(kind, chain), request, env=ENV,
                                             adapters=adapters, on_log=log.append, sleep_fn=lambda s: None, **kw)
    return result, log


def test_a_prompt_over_its_links_limit_is_never_sent_and_the_chain_moves_on():
    cloudflare, pollinations = Adapter(), Adapter()
    limiter = Limiter()
    request = GenRequest(kind="image", prompt="a" * 2049)  # Workers AI's flux-1-schnell caps prompt at 2048

    (result, link), log = _run("image", "cloudflare/flux-1-schnell,pollinations/flux", request,
                               {("image", "cloudflare"): cloudflare, ("image", "pollinations"): pollinations},
                               limiter=limiter)

    assert cloudflare.calls == [] and link.provider == "pollinations"
    assert limiter.asked == ["pollinations"]  # the refused link took no slot of its free tier
    line = next(line for line in log if "cloudflare/flux-1-schnell" in line)
    assert "⏭" in line and "2048 characters" in line and "2049" in line


def test_a_refused_paid_link_is_never_estimated_or_budgeted():
    kling, budgeted = Adapter(est=0.21), []
    request = GenRequest(kind="video", prompt="a" * 3120, duration_s=5, seed=1, references=("k.png",))
    with pytest.raises(NoRunnableLink) as excinfo:
        _run("video", "fal/kling-2.5-turbo-std", request, {("video", "fal"): kling},
             budget_check=lambda est, l: budgeted.append(l))
    assert kling.calls == [] and budgeted == []
    [(label, reason)] = excinfo.value.failures
    assert label == "fal/kling-2.5-turbo-std"
    assert reason.startswith(prompt_limits.REFUSAL_HEAD)
    assert "fal/kling-2.5-turbo-std accepts 2500 characters; this prompt is 3120" in reason
    assert "accepts 2500 characters" in str(excinfo.value)


def test_a_tts_line_is_measured_by_its_text():
    gemini = Adapter()
    request = GenRequest(kind="tts", text="mot " * 9000)
    with pytest.raises(NoRunnableLink) as excinfo:
        _run("tts", "gemini/flash-lite-tts", request, {("tts", "gemini"): gemini})
    assert gemini.calls == [] and "tokens" in str(excinfo.value)
    (result, link), _ = _run("tts", "gemini/flash-lite-tts", GenRequest(kind="tts", text="Bonjour !"),
                             {("tts", "gemini"): gemini})
    assert link.model == "flash-lite-tts" and len(gemini.calls) == 1


def test_a_prompt_within_the_limit_runs_as_before():
    kling = Adapter(est=0.21)
    request = GenRequest(kind="video", prompt="a" * 2500, duration_s=5, seed=1, references=("k.png",))
    (result, link), _ = _run("video", "fal/kling-2.5-turbo-std", request, {("video", "fal"): kling})
    assert kling.calls == [KLING] and result.paid is True


def test_a_refusal_writes_no_journal_entry(tmp_path):
    """With a generation cache the request has a key; a refusal begins nothing."""
    from clipping.providers import gencache

    keyframe = tmp_path / "k.png"
    keyframe.write_bytes(b"\x89PNG")
    kling, books = Adapter(est=0.21), []
    request = GenRequest(kind="video", prompt="a" * 3000, duration_s=5, seed=1, references=(str(keyframe),),
                         out_dir=str(tmp_path / "out"))
    cache = gencache.GenCache(tmp_path / "gen", book=books.append)
    assert cache.key("video", KLING, request) is not None
    with pytest.raises(NoRunnableLink):
        _run("video", "fal/kling-2.5-turbo-std", request, {("video", "fal"): kling}, cache=cache)
    assert kling.calls == [] and books == []
    journal = tmp_path / "gen"
    assert not journal.exists() or not [path for path in journal.iterdir() if path.suffix == ".json"]


def test_the_story_side_does_not_read_a_refusal_as_a_paid_call_sent_or_a_link_gone():
    """A refusal costs nothing: a free link's rate limit still holds the item
    for a later pass (pacing), and the refused link stays the episode's
    (sticky_link) -- only that one prompt was too long for it."""
    from clipping.aistory.steps import pacing as story_pacing, sticky_link

    refusal = f"{prompt_limits.REFUSAL_HEAD}fal/flux-schnell accepts 512 tokens; this prompt is about 600"
    failures = [("pollinations/flux", "HttpStatusError: HTTP 429 from https://image.pollinations.ai/prompt/x"),
                ("fal/flux-schnell", refusal)]
    assert story_pacing.rate_limited_by(failures) == "pollinations"
    assert sticky_link.gone_why([("fal/flux-schnell", refusal)], "fal/flux-schnell") is None
