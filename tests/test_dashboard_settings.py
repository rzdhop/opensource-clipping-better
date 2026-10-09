"""The Settings page is Clips only: the analysis keys and their test, the
custom endpoint, the B-roll sources and the system facts, on one page.

The AI Story tabs (images, video & voices; local hardware; budget) are gone,
and with them every field and route the backend no longer serves. Text
guards over the source, like the other page guards (DEC-012).
"""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PAGE = (ROOT / "web" / "dashboard" / "src" / "pages" / "Settings.jsx").read_text(encoding="utf-8")
CSS = (ROOT / "web" / "dashboard" / "src" / "index.css").read_text(encoding="utf-8")

# What the page still writes with PUT /api/settings.
KEPT_PAYLOAD_FIELDS = (
    "google_api_key", "groq_api_key", "nvidia_api_key", "openrouter_api_key", "mistral_api_key",
    "openai_compat_api_key", "gemini_paid_api_key", "anthropic_api_key",
    "pexels_api_key", "pixabay_api_key", "hf_token",
    "broll_sources", "broll_local_dir",
    "openai_compat_base_url", "openai_compat_model", "allow_slow_chain",
)

# Settings fields the backend removed with AI Story: none may be read or sent.
REMOVED_FIELDS = (
    "story_llm_chain", "story_llm_premium_chain", "allow_paid", "per_episode_cap_usd",
    "daily_cap_usd", "per_story_cap_usd", "budget_profile", "budget_timezone",
    "effective_budget_profile", "spend_", "day_extra_usd", "daily_cap_below_spend",
    "day_contributors", "generation_chains", "usage_today", "fal_key", "openai_api_key",
    "cloudflare_api_token", "cloudflare_account_id", "pollinations_api_key",
    "elevenlabs_api_key", "runpod_api_key", "runpod_comfy_endpoint_id",
    "runpod_gpu_usd_per_hour", "local_comfyui_url", "local_ollama_url",
)


def test_the_page_imports_only_the_clips_settings_calls():
    match = re.search(r"import \{([^}]*)\} from '\.\./api'", PAGE)
    assert match, "Settings.jsx does not import from ../api"
    imported = {name.strip() for name in match.group(1).split(",") if name.strip()}
    assert imported == {"fetchBrollStatus", "fetchSettings", "testChain", "updateSettings"}


def test_every_kept_field_is_still_sent():
    for field in KEPT_PAYLOAD_FIELDS:
        assert f"payload.{field} =" in PAGE, field


def test_no_removed_field_is_left():
    for field in REMOVED_FIELDS:
        assert field not in PAGE, field


def test_there_are_no_tabs_left():
    assert "SETTINGS_TABS" not in PAGE
    assert "role=\"tablist\"" not in PAGE and "role=\"tabpanel\"" not in PAGE
    assert "rzc_settings_tab" not in PAGE
    assert ".settings-tabs" not in CSS and ".settings-tab." not in CSS


def test_the_keys_keep_their_badges_and_the_chain_test_stays():
    assert "const KeyBadge" in PAGE
    for key in ("groq_api_key_set", "google_api_key_set", "gemini_paid_api_key_set", "anthropic_api_key_set"):
        assert key in PAGE, key
    assert "await testChain()" in PAGE
    assert 'title="System info"' in PAGE
