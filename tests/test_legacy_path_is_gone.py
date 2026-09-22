"""The single-request analysis path is deleted, and what replaced it.

`engine.get_analysis_prompt` asked one model for 22 required fields per clip --
roughly 1200 output tokens each, from providers measured at 12-13 tokens/s
behind a ~300s gateway. Seven clips needed ~660s and could not finish, and it
exceeded Groq's 8000 tokens/minute outright. Per `analyzer.py`'s own docstring,
every job that ever ran it failed. It sat unreachable behind `--ai-provider
nvidia|gemini` while the chain did the real work, and its 330-line Indonesian
prompt was the file people kept finding first when they went looking for "the
prompt".

What is NOT deleted matters as much: `engine.py` still owns transcription and
re-exports the transcript parsers, and four other test modules pin exactly
those symbols.

Read as text rather than imported: `clipping.engine` pulls in `faster_whisper`
lazily but `web.api.models` pulls in pydantic, and CI installs pytest and
nothing else (DEC-012). An importorskip guard never runs in the one environment
that checks every push.
"""

import pathlib

import pytest

from clipping.config import PROVIDER_KEYS, build_config

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
ENGINE = PROJECT_ROOT / "clipping" / "engine.py"
MODELS = PROJECT_ROOT / "web" / "api" / "models.py"
CONFIG = PROJECT_ROOT / "clipping" / "config.py"
NEW_JOB = PROJECT_ROOT / "web" / "dashboard" / "src" / "pages" / "NewJob.jsx"


def engine_source():
    return ENGINE.read_text(encoding="utf-8")


# --------------------------------------------------------------- what went

GONE = (
    "def get_analysis_prompt",
    "def analyze_with_nvidia",
    "def analyze_with_gemini",
    "def analyze_with_openai_compat",
    "def _analyze_openai_compatible",
    "def _build_account_classification_prompt",
    "def _generate_json_with_retry",
    "TARGET_ACCOUNTS",
    "klasifikasi_akun",
    "MIN_CLIP_DURATION",
)


@pytest.mark.parametrize("symbol", GONE)
def test_the_legacy_analysis_path_is_gone(symbol):
    assert symbol not in engine_source(), (
        f"{symbol} is part of the single-request path that never produced a "
        f"clip set; it should have gone with it."
    )


def test_the_indonesian_prompt_is_gone():
    """Every prompt in this project is English and lives in prompts.py."""
    source = engine_source()
    for indonesian in ("Kamu adalah", "ATURAN", "Berikan analisis", "durasi_hook:"):
        assert indonesian not in source


def test_the_legacy_retry_ladder_is_gone():
    """`llm.py` owns retries now, and its policy is asserted elsewhere."""
    source = engine_source()
    assert "NVIDIA_MAX_ATTEMPTS" not in source
    assert "_nvidia_is_retryable" not in source


def test_engine_is_no_longer_enormous():
    """It was ~1590 lines, of which the live path used about forty."""
    assert len(engine_source().splitlines()) < 400


# ------------------------------------------------------------- what stayed

KEPT = (
    "def transcribe_video",
    "def load_whisper_model",
    "def estimate_cpu_transcription_seconds",
    "def _warn_if_cpu_transcription_will_be_slow",
    "CPU_WHISPER_REALTIME_FACTOR",
    "def analyze_with_ai",
    # Re-exported from clipping/transcript.py; four test modules reach for
    # engine.load_transcript alongside engine.transcribe_video.
    "load_transcript",
    "parse_vtt_subs",
    "parse_youtube_json3_subs",
    "TranscriptParseError",
)


@pytest.mark.parametrize("symbol", KEPT)
def test_transcription_and_the_parsers_survive(symbol):
    assert symbol in engine_source(), (
        f"{symbol} is not part of the legacy analysis path and is still used"
    )


def test_the_dispatcher_only_knows_the_chain_now():
    source = engine_source()
    assert "analysis import analyzer" in source or "analysis.analyzer" in source


# ----------------------------------------------- openai_compat, as an alias

def test_openai_compat_becomes_a_one_link_custom_chain(tmp_path, monkeypatch):
    """DEC-046 kept two custom-endpoint paths so that collapsing them could not
    silently change the meaning of an existing OPENAI_COMPAT_* setup. The
    monolith goes; the setup keeps working."""
    monkeypatch.delenv("LLM_CUSTOM_BASE_URL", raising=False)
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    cfg = build_config([
        "--video", str(video),
        "--ai-provider", "openai_compat",
        "--openai-compat-base-url", "https://example.test/v1",
        "--openai-compat-model", "some-model",
    ])

    assert cfg.ai_provider == "chain"
    assert cfg.llm_chain == "custom/some-model"


def test_the_openai_compat_key_reaches_the_custom_provider(tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_CUSTOM_BASE_URL", raising=False)
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "secret-from-env")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    cfg = build_config([
        "--video", str(video),
        "--ai-provider", "openai_compat",
        "--openai-compat-base-url", "https://example.test/v1",
        "--openai-compat-model", "some-model",
    ])

    assert cfg.api_key_custom == "secret-from-env"


def test_an_explicit_llm_custom_base_url_is_not_overwritten(tmp_path, monkeypatch):
    """Someone who set the chain's own variable meant it."""
    monkeypatch.setenv("LLM_CUSTOM_BASE_URL", "https://mine.test/v1")
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    build_config([
        "--video", str(video),
        "--ai-provider", "openai_compat",
        "--openai-compat-base-url", "https://example.test/v1",
        "--openai-compat-model", "some-model",
    ])

    import os
    assert os.environ["LLM_CUSTOM_BASE_URL"] == "https://mine.test/v1"


def test_the_openai_compat_gate_still_fails_fast_without_a_model(tmp_path):
    """A half-configured endpoint fails at 40ms, not after transcription."""
    from clipping.config import missing_provider_key

    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")

    cfg = build_config([
        "--video", str(video),
        "--ai-provider", "openai_compat",
        "--openai-compat-base-url", "https://example.test/v1",
    ])
    assert missing_provider_key(cfg) is not None


# -------------------------------------------------- the surfaces that named it

def test_the_cli_no_longer_offers_the_legacy_providers():
    source = CONFIG.read_text(encoding="utf-8")
    assert '"chain", "gemini", "nvidia", "openai_compat"' not in source


def test_the_dashboard_no_longer_offers_the_legacy_providers():
    source = NEW_JOB.read_text(encoding="utf-8")
    assert 'value="nvidia"' not in source
    assert 'value="gemini"' not in source


def test_the_request_model_no_longer_offers_the_legacy_providers():
    source = MODELS.read_text(encoding="utf-8")
    assert 'NVIDIA = "nvidia"' not in source
    assert 'GEMINI = "gemini"' not in source


def test_nvidia_and_gemini_keys_stay_in_the_registry():
    """They are chain providers. Only their --ai-provider meaning went."""
    assert "nvidia" in PROVIDER_KEYS
    assert "gemini" in PROVIDER_KEYS
    assert "openai_compat" in PROVIDER_KEYS
