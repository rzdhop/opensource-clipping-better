"""Veo bills a separate, billing-enabled Google project (AI Story phase 6,
RC-V4): ``gemini/veo-3.1-lite`` reads ``GEMINI_PAID_API_KEY`` and never
``GOOGLE_API_KEY``, and no other gemini link -- the free text, vision and
TTS ones, and the nano-banana images for now -- ever reads
``GEMINI_PAID_API_KEY``. The Settings store keeps and masks the new key
like ``FAL_KEY``. Stdlib + pytest only (DEC-012).
"""

import pytest

from clipping.providers import budget, gating
from clipping.providers.generation import (
    GenRequest, GenResult, NoRunnableLink, parse_generation_chain, run_generation_chain,
)
from clipping.providers.registry import Link
from web.api import settings_store

VEO = "gemini/veo-3.1-lite"


class Recorder:
    """A gemini adapter that answers and records the credentials it was handed."""

    def __init__(self):
        self.credentials = []

    def estimate(self, link, request):
        return 0.2

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.credentials.append(dict(credentials))
        return GenResult(provider=link.provider, model=link.model, paths=("out",))


def run(kind, chain, adapter, env):
    return run_generation_chain(kind, parse_generation_chain(kind, chain), GenRequest(kind=kind, prompt="p", text="t"),
                                env=env, allow_paid=True, adapters={(kind, "gemini"): adapter},
                                on_log=lambda line: None)


def test_veo_with_only_the_free_key_is_skipped_naming_the_paid_one():
    veo = Recorder()
    with pytest.raises(NoRunnableLink) as excinfo:
        run("video", VEO, veo, {"GOOGLE_API_KEY": "free"})
    assert veo.credentials == []
    assert excinfo.value.failures == [(VEO, "no API key (GEMINI_PAID_API_KEY is not set)")]

    summary = gating.link_summary("video", Link("gemini", "veo-3.1-lite"), {"GOOGLE_API_KEY": "free"},
                                  budget.budget_from_env({}), GenRequest(kind="video"), adapters={})
    assert summary["env_keys"] == summary["missing_keys"] == ["GEMINI_PAID_API_KEY"]
    assert summary["keyed"] is False and summary["allowed"] is False


@pytest.mark.parametrize("kind,chain,handed", [
    ("video", VEO, {"GEMINI_PAID_API_KEY": "paid"}),
    ("tts", "gemini/flash-lite-tts", {"GOOGLE_API_KEY": "free"}),
    ("vision", "gemini/flash-lite", {"GOOGLE_API_KEY": "free"}),
    ("image_edit", "gemini/nano-banana-2-lite", {"GOOGLE_API_KEY": "free"}),
], ids=["veo", "tts", "vision", "nano-banana"])
def test_each_gemini_link_is_handed_its_own_key_and_never_the_other(kind, chain, handed):
    adapter = Recorder()
    run(kind, chain, adapter, {"GOOGLE_API_KEY": "free", "GEMINI_PAID_API_KEY": "paid"})
    assert adapter.credentials == [handed]


def test_the_paid_key_is_persisted_and_masked_like_fal_key(tmp_path):
    path = str(tmp_path / "settings.json")
    values = {"FAL_KEY": "fk", "GEMINI_PAID_API_KEY": "pk"}
    assert settings_store.save(values, path) is True
    assert settings_store.load(path) == values
    assert settings_store.redact(values) == {"FAL_KEY": "***", "GEMINI_PAID_API_KEY": "***"}
