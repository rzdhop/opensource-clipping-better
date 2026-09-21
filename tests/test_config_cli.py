"""Tests for the local-first CLI surface.

``build_config`` calls ``os.getcwd()`` and ``os.makedirs`` for ``outputs/`` and
``custom_fonts/``, so every test runs inside ``tmp_path``.
"""

import os

import pytest

from clipping.config import build_config


@pytest.fixture
def workdir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    return tmp_path


@pytest.fixture
def video(workdir):
    path = workdir / "sample.mp4"
    path.write_bytes(b"\x00")
    return path


@pytest.fixture
def vtt(workdir):
    path = workdir / "sample.vtt"
    path.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello world\n", encoding="utf-8"
    )
    return path


# ------------------------------------------------------------------ happy path

def test_video_sets_file_video_asli(video):
    cfg = build_config(["--video", str(video)])

    assert cfg.file_video_asli == os.path.abspath(str(video))
    assert os.path.isabs(cfg.file_video_asli)
    assert cfg.video_provided is True
    assert cfg.transcript_path is None
    assert cfg.no_whisper is False
    assert cfg.transcript_offset == 0.0
    assert cfg.source_url is None


def test_transcript_is_absolute(video, vtt):
    cfg = build_config(["--video", str(video), "--transcript", str(vtt)])

    assert cfg.transcript_path == os.path.abspath(str(vtt))
    assert os.path.isabs(cfg.transcript_path)


def test_relative_paths_are_absolutised(workdir, video, vtt):
    """Layer 4 (ffmpeg/OpenCV) must receive absolute paths regardless of input."""
    cfg = build_config(["--video", "sample.mp4", "--transcript", "sample.vtt"])

    assert cfg.file_video_asli == str(workdir / "sample.mp4")
    assert cfg.transcript_path == str(workdir / "sample.vtt")


def test_optional_flags(video, vtt):
    cfg = build_config([
        "--video", str(video),
        "--transcript", str(vtt),
        "--transcript-offset", "2.5",
        "--no-whisper",
        "--source-url", "Podcast XYZ ep.42",
    ])

    assert cfg.transcript_offset == 2.5
    assert cfg.no_whisper is True
    # Attribution is free text, never fetched, never validated as a URL.
    assert cfg.source_url == "Podcast XYZ ep.42"


def test_short_flags(video, vtt):
    cfg = build_config(["-v", str(video), "-t", str(vtt)])
    assert cfg.file_video_asli == os.path.abspath(str(video))
    assert cfg.transcript_path == os.path.abspath(str(vtt))


# -------------------------------------------------------------- fail-fast path

def test_no_input_at_all_errors(workdir):
    with pytest.raises(SystemExit):
        build_config([])


def test_missing_video_errors(workdir):
    with pytest.raises(SystemExit):
        build_config(["--video", "nope.mp4"])


def test_missing_transcript_errors(video):
    with pytest.raises(SystemExit):
        build_config(["--video", str(video), "--transcript", "nope.vtt"])


def test_transcript_without_video_errors(vtt):
    with pytest.raises(SystemExit):
        build_config(["--transcript", str(vtt)])


def test_no_whisper_without_transcript_errors(video):
    with pytest.raises(SystemExit):
        build_config(["--video", str(video), "--no-whisper"])


def test_unsupported_video_extension_errors(workdir):
    bad = workdir / "sample.txt"
    bad.write_bytes(b"\x00")
    with pytest.raises(SystemExit):
        build_config(["--video", str(bad)])


def test_unsupported_transcript_extension_errors(workdir, video):
    bad = workdir / "sample.txt"
    bad.write_text("nope", encoding="utf-8")
    with pytest.raises(SystemExit):
        build_config(["--video", str(video), "--transcript", str(bad)])


def test_directory_is_not_a_video(workdir):
    """os.path.isfile, not os.path.exists -- a directory must not pass."""
    d = workdir / "a_directory.mp4"
    d.mkdir()
    with pytest.raises(SystemExit):
        build_config(["--video", str(d)])


def test_uppercase_extension_accepted(workdir):
    upper = workdir / "SAMPLE.MP4"
    upper.write_bytes(b"\x00")
    cfg = build_config(["--video", str(upper)])
    assert cfg.file_video_asli == os.path.abspath(str(upper))


# ------------------------------------------------------------------- purged

@pytest.mark.parametrize(
    "argv",
    [
        ["--url", "https://youtube.com/watch?v=x"],
        ["-u", "https://youtube.com/watch?v=x"],
        ["--source", "tiktok"],
        ["--tiktok"],
        ["--source-height", "1080"],
        ["--use-dlp-subs"],
        ["--skip-download"],
    ],
)
def test_download_flags_are_gone(workdir, video, argv):
    """The download layer is removed; these flags must not silently no-op."""
    with pytest.raises(SystemExit):
        build_config(["--video", str(video), *argv])


def test_no_download_attributes_on_cfg(video):
    cfg = build_config(["--video", str(video)])
    for attr in (
        "url_youtube",
        "source_platform",
        "download_source_height",
        "use_dlp_subs",
        "skip_download",
    ):
        assert not hasattr(cfg, attr), f"cfg.{attr} survived the purge"


# --------------------------------------------------------------- audio path

@pytest.mark.parametrize(
    "name", ["sample.mp4", "SAMPLE.MP4", "My Talk.mkv", "clip.mov", "no_ext_case.webm"]
)
def test_derive_audio_path_handles_any_extension(tmp_path, name):
    """The old cfg.file_video_asli.replace('.mp4', ...) silently no-opped on
    anything that was not lowercase .mp4, leaving audio_path == video_path."""
    from clipping.diarization import derive_audio_path

    video = str(tmp_path / name)
    out = derive_audio_path(video, str(tmp_path / "outputs"))

    assert out != video
    assert out.endswith("_audio.wav")
    assert os.path.dirname(out) == str(tmp_path / "outputs")


def test_derive_audio_path_defaults_beside_source(tmp_path):
    from clipping.diarization import derive_audio_path

    video = str(tmp_path / "sample.mp4")
    assert derive_audio_path(video) == str(tmp_path / "sample_audio.wav")


# ------------------------------------------------------------- AI provider

def test_default_provider_is_nvidia(video):
    cfg = build_config(["--video", str(video)])
    assert cfg.ai_provider == "nvidia"
    # Pinned so a model retirement shows up as a test failure, not a 410 in
    # production. deepseek-v4-pro died on 2026-08-07 exactly this way.
    assert cfg.nvidia_model == "nvidia/nemotron-3-super-120b-a12b"


def test_provider_can_be_overridden(video):
    cfg = build_config(["--video", str(video), "--ai-provider", "gemini"])
    assert cfg.ai_provider == "gemini"


PROVIDER_CASES = [
    ("nvidia", "api_key_nvidia", "NVIDIA_API_KEY"),
    ("gemini", "api_key_gemini", "GOOGLE_API_KEY"),
    ("openai_compat", "api_key_openai_compat", "OPENAI_COMPAT_API_KEY"),
]


@pytest.mark.parametrize("provider,key_attr,env_name", PROVIDER_CASES)
def test_missing_provider_key_names_the_right_env_var(
    video, monkeypatch, provider, key_attr, env_name
):
    from clipping.config import missing_provider_key

    cfg = build_config(["--video", str(video), "--ai-provider", provider])
    setattr(cfg, key_attr, "")
    assert missing_provider_key(cfg) == (key_attr, env_name)

    setattr(cfg, key_attr, "a-key")
    # A custom endpoint needs a base URL and a model too, so satisfy those
    # before asserting the gate is clear.
    if provider == "openai_compat":
        cfg.openai_compat_base_url = "https://example.test/v1"
        cfg.openai_compat_model = "some-model"
    assert missing_provider_key(cfg) is None


def test_every_provider_is_covered_by_the_parametrisation():
    """A new provider added to PROVIDER_KEYS without a case here would go
    entirely untested -- and the gate failing open means jobs die late."""
    from clipping.config import PROVIDER_KEYS

    assert {case[0] for case in PROVIDER_CASES} == set(PROVIDER_KEYS)


@pytest.mark.parametrize(
    "attr,env_name",
    [
        ("openai_compat_base_url", "OPENAI_COMPAT_BASE_URL"),
        ("openai_compat_model", "OPENAI_COMPAT_MODEL"),
    ],
)
def test_custom_endpoint_needs_more_than_a_key(video, attr, env_name):
    """A key alone cannot reach an endpoint whose URL or model is unset."""
    from clipping.config import missing_provider_key

    cfg = build_config(["--video", str(video), "--ai-provider", "openai_compat"])
    cfg.api_key_openai_compat = "a-key"
    cfg.openai_compat_base_url = "https://example.test/v1"
    cfg.openai_compat_model = "some-model"
    assert missing_provider_key(cfg) is None

    setattr(cfg, attr, "")
    assert missing_provider_key(cfg) == (attr, env_name)


def test_custom_endpoint_reads_flags_and_env(video, monkeypatch):
    monkeypatch.setenv("OPENAI_COMPAT_BASE_URL", "https://from-env.test/v1")
    monkeypatch.setenv("OPENAI_COMPAT_MODEL", "env-model")
    monkeypatch.setenv("OPENAI_COMPAT_API_KEY", "env-key")

    cfg = build_config(["--video", str(video), "--ai-provider", "openai_compat"])
    assert cfg.openai_compat_base_url == "https://from-env.test/v1"
    assert cfg.openai_compat_model == "env-model"
    assert cfg.api_key_openai_compat == "env-key"

    cfg = build_config([
        "--video", str(video),
        "--ai-provider", "openai_compat",
        "--openai-compat-base-url", "https://from-flag.test/v1",
        "--openai-compat-model", "flag-model",
    ])
    assert cfg.openai_compat_base_url == "https://from-flag.test/v1"
    assert cfg.openai_compat_model == "flag-model"


def test_missing_provider_key_ignores_the_other_providers_key(video):
    """The old gate checked GOOGLE_API_KEY unconditionally, which would fail
    every NVIDIA run the moment NVIDIA became the default."""
    from clipping.config import missing_provider_key

    cfg = build_config(["--video", str(video), "--ai-provider", "nvidia"])
    cfg.api_key_nvidia = "nv-key"
    cfg.api_key_gemini = ""

    assert missing_provider_key(cfg) is None
