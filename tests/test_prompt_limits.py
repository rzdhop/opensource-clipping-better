"""Every provider's prompt size limit, known and enforced (the human,
2026-10-02: "some providers have limited prompt size so check that also").

``clipping.providers.prompt_limits`` holds one limit per link (published by
the vendor, or a conservative choice of ours, said so), measures a prompt
against it (``fits``), derives a word budget for the prompt builders
(``budget_words``), reads fal's published OpenAPI schema for a prompt's
``maxLength`` and keeps what it read next to the Settings file, which the
table then defers to. The runner's refusal is
tests/test_prompt_limits_dispatch.py; the key check's,
tests/test_prompt_limits_key_check.py. Every answer here is a fake
transport's. Stdlib + pytest.
"""

from __future__ import annotations

import json
import os
import urllib.parse

import pytest

from clipping.providers import generation, images, pacing as rate_pacing, prompt_limits, tts, video
from clipping.providers.generation import parse_generation_chain
from clipping.providers.registry import Link
from clipping.providers.transport import APIConnectionError, Response

KLING = Link("fal", "kling-2.5-turbo-std")
KLING_APP = "fal-ai/kling-video/v2.5-turbo/standard/image-to-video"
SEEDANCE = Link("fal", "seedance-1-pro-fast")
SEEDANCE_APP = "fal-ai/bytedance/seedance/v1/pro/fast/image-to-video"
NO_LIVE = {}  # table values only, whatever a real data/provider_limits.json holds


@pytest.fixture(autouse=True)
def _isolated_live_file(tmp_path, monkeypatch):
    """The live file follows the Settings file's folder: a test never reads or
    writes the repository's own data/."""
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))



# ------------------------------------------------------------------ the table

def _image_links():
    labels = [f"cloudflare/{m}" for m in images.CLOUDFLARE_MODELS]
    labels += [f"gemini/{m}" for m in images.GEMINI_MODELS]
    labels += [f"fal/{m}" for m in images.FAL_APPS]
    labels += [f"openai/{m}" for m in images.OPENAI_MODELS]
    return labels + ["pollinations/flux"]


@pytest.mark.parametrize("label", _image_links() + list(video.CLIP_LENGTHS)
                         + [f"gemini/{m}" for m in tts.GEMINI_TTS_MODELS])
def test_every_hosted_image_video_and_gemini_tts_link_has_a_limit_with_its_source(label):
    limit = prompt_limits.limit_for(label, live=NO_LIVE)
    assert limit is not None, label
    assert limit.max_chars or limit.max_tokens or limit.max_words or limit.window_tokens, label
    assert limit.source.strip(), label
    assert isinstance(limit.verified, bool)


@pytest.mark.parametrize(("label", "chars", "tokens", "window", "verified"), [
    ("fal/kling-2.5-turbo-std", 2500, None, None, True),
    ("fal/ltx-2-fast", 5000, None, None, True),
    ("gemini/veo-3.1-lite", None, 1024, None, True),
    ("cloudflare/flux-1-schnell", 2048, None, None, True),
    ("fal/flux-schnell", None, None, 512, False),
    ("fal/seedance-1-pro-fast", 1500, None, None, False),
])
def test_the_known_numbers(label, chars, tokens, window, verified):
    limit = prompt_limits.limit_for(label, live=NO_LIVE)
    assert (limit.max_chars, limit.max_tokens, limit.window_tokens, limit.verified) == (chars, tokens, window,
                                                                                       verified)


def test_a_text_encoders_window_bounds_the_budget_never_the_check():
    """FLUX's T5 reads ~512 tokens and ignores the rest; the API takes more.
    A v1 shot prompt past the window was always sent and served, so it still
    fits; the builders' budget stays inside the window."""
    long_v1_prompt = "word " * 440  # 2200 characters, ~550 tokens
    for label in ("fal/flux-schnell", "pollinations/flux", "fal/flux-kontext-pro"):
        assert prompt_limits.fits(label, long_v1_prompt, live=NO_LIVE)[0], label
        assert prompt_limits.budget_words(label, default=1000, live=NO_LIVE) == int(512 * 4 / 6.5), label


def test_an_unpublished_limit_says_so_in_its_source():
    for label, limit in prompt_limits.TABLE.items():
        if not limit.verified and (limit.max_chars or limit.max_tokens or limit.max_words):
            assert "unpublished" in limit.source or "our" in limit.source or "assumed" in limit.source, label


def test_a_link_or_its_label_and_a_provider_wide_entry():
    assert prompt_limits.limit_for(KLING, live=NO_LIVE) == prompt_limits.limit_for("fal/kling-2.5-turbo-std",
                                                                                    live=NO_LIVE)
    edge = prompt_limits.limit_for(Link("edge", "fr-FR-HenriNeural"), live=NO_LIVE)
    assert edge is not None and not (edge.max_chars or edge.max_tokens or edge.max_words)
    assert "splits" in edge.source
    assert prompt_limits.limit_for("local/comfyui", live=NO_LIVE) is None
    assert prompt_limits.limit_for("fal/not-a-model", live=NO_LIVE) is None


# ------------------------------------------------------- fits / budget_words

def test_fits_measures_characters_words_and_tokens():
    ok, measured = prompt_limits.fits(KLING, "a" * 2500, live=NO_LIVE)
    assert ok and measured["chars"] == 2500 and measured["words"] == 1
    assert measured["tokens"] == rate_pacing.estimate_tokens("a" * 2500)
    ok, measured = prompt_limits.fits(KLING, "a" * 2501, live=NO_LIVE)
    assert not ok and measured["chars"] == 2501


def test_a_token_limit_is_measured_with_the_pacing_estimate():
    """Veo is documented in tokens: ~4 characters each (pacing.estimate_tokens)."""
    assert prompt_limits.fits("gemini/veo-3.1-lite", "x" * 4096, live=NO_LIVE)[0]
    ok, measured = prompt_limits.fits("gemini/veo-3.1-lite", "x" * 4100, live=NO_LIVE)
    assert not ok and measured["tokens"] == 1025


def test_pollinations_counts_the_prompt_as_it_rides_in_the_url():
    limit = prompt_limits.limit_for("pollinations/flux", live=NO_LIVE)
    assert limit.measure == prompt_limits.URL
    text = "é " * (limit.max_chars // 8)  # 2 characters, 9 once encoded ("%C3%A9%20")
    ok, measured = prompt_limits.fits("pollinations/flux", text, live=NO_LIVE)
    assert measured["url_chars"] == len(urllib.parse.quote(text, safe=""))
    assert measured["chars"] < limit.max_chars < measured["url_chars"]
    assert not ok


def test_check_names_the_link_the_measured_size_and_the_limit():
    with pytest.raises(prompt_limits.PromptTooLong) as excinfo:
        prompt_limits.check(KLING, "a" * 3120, live=NO_LIVE)
    assert str(excinfo.value) == "fal/kling-2.5-turbo-std accepts 2500 characters; this prompt is 3120"
    assert excinfo.value.label == "fal/kling-2.5-turbo-std" and excinfo.value.measured["chars"] == 3120
    assert isinstance(excinfo.value, generation.errors.ProviderError)
    with pytest.raises(prompt_limits.PromptTooLong) as excinfo:
        prompt_limits.check("gemini/veo-3.1-lite", "x" * 4400, live=NO_LIVE)
    assert "1024 tokens" in str(excinfo.value) and "1100" in str(excinfo.value)
    assert prompt_limits.check(KLING, "a short prompt", live=NO_LIVE)["chars"] == 14


def test_budget_words_is_the_documented_formula():
    """chars / 6.5 (French/English prose with accents, a margin over the ~6.0
    measured); tokens x 4 / 6.5; a URL-encoded cap / 10; the smallest wins."""
    assert prompt_limits.budget_words(KLING, default=80, live=NO_LIVE) == 2500 * 2 // 13
    assert prompt_limits.budget_words("gemini/veo-3.1-lite", default=80, live=NO_LIVE) == int(1024 * 4 / 6.5)
    pollinations = prompt_limits.limit_for("pollinations/flux", live=NO_LIVE)
    assert prompt_limits.budget_words("pollinations/flux", default=80, live=NO_LIVE) == int(min(
        pollinations.max_chars / 10, pollinations.window_tokens * 4 / 6.5))
    assert prompt_limits.budget_words("edge/fr-FR-HenriNeural", default=80, live=NO_LIVE) == 80
    assert prompt_limits.budget_words("local/comfyui", default=220, live=NO_LIVE) == 220


def test_todays_prompt_budgets_fit_every_default_link():
    """The builders' fixed budgets (keyframe 220 words, clip 80) fit every
    default link today: enforcement changes nothing for a prompt they wrote."""
    for kind, budget in (("image", 283), ("image_edit", 283), ("video", 80)):
        for link in parse_generation_chain(kind, generation.DEFAULT_CHAINS[kind]):
            assert prompt_limits.budget_words(link, default=budget, live=NO_LIVE) >= budget, link


# ------------------------------------------------------------- live limits

class Routes:
    """One answer per URL fragment; records every request."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, method, url, *, headers=None, body=None, timeout=60):
        self.calls.append({"method": method, "url": url, "headers": dict(headers or {}), "body": body})
        for fragment, answer in self.routes.items():
            if fragment in url:
                if isinstance(answer, Exception):
                    raise answer
                status, payload = answer
                data = json.dumps(payload).encode() if isinstance(payload, (dict, list)) else payload
                return Response(status, {}, data)
        raise AssertionError(f"unexpected request {method} {url}")


def fal_schema(endpoint, prompt):
    """The shape of fal's queue OpenAPI document: the POST on the endpoint's
    path takes ``#/components/schemas/<X>Input``."""
    return {
        "openapi": "3.0.4",
        "paths": {
            f"/{endpoint}/requests/{{request_id}}/status": {"get": {}},
            f"/{endpoint}": {"post": {"requestBody": {"required": True, "content": {"application/json": {
                "schema": {"$ref": "#/components/schemas/ModelInput"}}}}}},
        },
        "components": {"schemas": {
            "QueueStatus": {"type": "object", "properties": {"status": {"type": "string"}}},
            "ModelInput": {"type": "object", "properties": {"prompt": prompt, "image_url": {"type": "string"}}},
        }},
    }


SCHEMA = "fal.ai/api/openapi/queue/openapi.json"


def test_fal_schema_with_a_maxlength_is_published():
    transport = Routes({SCHEMA: (200, fal_schema(KLING_APP, {"type": "string", "maxLength": 2500}))})
    read = prompt_limits.read_fal_schema(KLING_APP, transport=transport)
    assert read["status"] == "published" and read["prompt_max_chars"] == 2500
    [call] = transport.calls
    assert call["method"] == "GET" and call["body"] is None
    assert call["url"] == f"https://fal.ai/api/openapi/queue/openapi.json?endpoint_id={KLING_APP}"
    assert "Authorization" not in call["headers"]  # public: no key goes to it


def test_fal_schema_maxlength_inside_anyof_is_read():
    prompt = {"anyOf": [{"type": "string", "maxLength": 1800}, {"type": "null"}]}
    read = prompt_limits.read_fal_schema(SEEDANCE_APP, transport=Routes({SCHEMA: (200, fal_schema(SEEDANCE_APP, prompt))}))
    assert read["status"] == "published" and read["prompt_max_chars"] == 1800


def test_fal_schema_without_a_maxlength_is_not_published():
    read = prompt_limits.read_fal_schema(SEEDANCE_APP, transport=Routes({SCHEMA: (200, fal_schema(
        SEEDANCE_APP, {"type": "string", "description": "The prompt"}))}))
    assert read["status"] == "not_published" and read["prompt_max_chars"] is None


@pytest.mark.parametrize("answer", [(404, {"detail": "not found"}), (503, "busy"), APIConnectionError("refused"),
                                    (200, {"not": "openapi"})])
def test_fal_schema_that_cannot_be_read_is_failed_not_raised(answer):
    read = prompt_limits.read_fal_schema(KLING_APP, transport=Routes({SCHEMA: answer}))
    assert read["status"] == "failed" and read["prompt_max_chars"] is None and read["text"]


def test_the_live_file_sits_next_to_the_settings_file(tmp_path):
    assert prompt_limits.live_path() == str(tmp_path / "provider_limits.json")


def test_the_default_live_file_is_in_the_dashboards_data_folder(monkeypatch):
    from web.api import settings_store

    monkeypatch.delenv("WEB_SETTINGS_FILE")
    assert prompt_limits.live_path() == os.path.join(settings_store.DATA_DIR, "provider_limits.json")


def test_a_live_value_is_stored_and_preferred_over_the_table(tmp_path):
    path = prompt_limits.record_live({
        "fal/seedance-1-pro-fast": {"endpoint": SEEDANCE_APP, "status": "published", "prompt_max_chars": 1800},
        "fal/kling-2.5-turbo-std": {"endpoint": KLING_APP, "status": "not_published", "prompt_max_chars": None},
    }, now="2026-10-02T10:00:00+00:00")
    stored = json.loads((tmp_path / "provider_limits.json").read_text(encoding="utf-8"))
    assert path == str(tmp_path / "provider_limits.json")
    assert stored["$schema"] == prompt_limits.LIVE_SCHEMA
    assert stored["links"]["fal/seedance-1-pro-fast"]["read_at"] == "2026-10-02T10:00:00+00:00"

    seedance = prompt_limits.limit_for(SEEDANCE)
    assert seedance.max_chars == 1800 and seedance.verified and "fal's schema" in seedance.source
    assert prompt_limits.limit_for(KLING) == prompt_limits.limit_for(KLING, live=NO_LIVE)  # not published: table
    assert not prompt_limits.fits(SEEDANCE, "a" * 1801)[0]


def test_a_later_read_merges_into_the_live_file(tmp_path):
    prompt_limits.record_live({"fal/seedance-1-pro-fast": {"status": "published", "prompt_max_chars": 1800}})
    prompt_limits.record_live({"fal/kling-2.5-turbo-std": {"status": "published", "prompt_max_chars": 2400}})
    assert prompt_limits.limit_for(SEEDANCE).max_chars == 1800
    assert prompt_limits.limit_for(KLING).max_chars == 2400


@pytest.mark.parametrize("content", ["{not json", json.dumps([1, 2]), json.dumps({"$schema": "other", "links": {}})])
def test_an_unreadable_live_file_is_ignored(tmp_path, content):
    (tmp_path / "provider_limits.json").write_text(content, encoding="utf-8")
    assert prompt_limits.read_live() == {}
    assert prompt_limits.limit_for(KLING) == prompt_limits.limit_for(KLING, live=NO_LIVE)


# --------------------------------------------------------------------- CLI

def test_the_cli_lists_every_links_limit_and_source(tmp_path, capsys):
    """``python main.py --ai-story prompt-limits``: one line per link, live
    values included, and where they are kept."""
    from clipping.aistory import cli

    prompt_limits.record_live({"fal/seedance-1-pro-fast": {"endpoint": SEEDANCE_APP, "status": "published",
                                                           "prompt_max_chars": 1800}},
                              now="2026-10-02T10:00:00+00:00")
    assert cli.main(["prompt-limits", "--outputs-dir", str(tmp_path / "outputs")]) == 0
    out = capsys.readouterr().out
    lines = {line.split(":", 1)[0].strip(): line for line in out.splitlines() if ":" in line}
    for label in prompt_limits.TABLE:
        assert label in lines, label
    assert "2500 chars" in lines["fal/kling-2.5-turbo-std"] and "published" in lines["fal/kling-2.5-turbo-std"]
    assert "1800 chars" in lines["fal/seedance-1-pro-fast"] and "fal's schema" in lines["fal/seedance-1-pro-fast"]
    assert "1024 tokens" in lines["gemini/veo-3.1-lite"] and "~630 words" in lines["gemini/veo-3.1-lite"]
    assert "our estimate" in lines["fal/seedream-4.5"]
    assert str(tmp_path / "provider_limits.json") in out


def test_the_cli_names_the_command_in_its_usage():
    from clipping.aistory import cli

    assert "prompt-limits" in cli.build_parser().format_usage()
