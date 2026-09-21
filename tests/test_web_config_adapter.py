"""Tests for the web API's config adapter.

Pure functions -- no server, no network. The adapter hand-rolls the same
namespace ``build_config`` produces, so it is the easiest place for the two to
drift; several tests below exist purely to catch that drift.

``build_config_from_payload`` derives ``base_dir`` from ``__file__`` rather than
the cwd, so it always resolves ``uploads/`` against the real project root and
``os.makedirs`` an ``outputs/<job_id>``. The fixtures therefore write
uniquely-named files into the real tree and clean up after themselves.
"""

import os
import pathlib
import shutil
import uuid

import pytest

from web.api.config_adapter import build_config_from_payload

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


@pytest.fixture
def upload_file():
    """Yield a factory: ``upload_file("a.vtt", "text")`` -> unique basename."""
    up = PROJECT_ROOT / "uploads"
    up.mkdir(exist_ok=True)
    created: list[pathlib.Path] = []

    def _make(suffix: str, content: bytes | str = b"\x00") -> str:
        name = f"test_{uuid.uuid4().hex[:8]}{suffix}"
        target = up / name
        if isinstance(content, str):
            target.write_text(content, encoding="utf-8")
        else:
            target.write_bytes(content)
        created.append(target)
        return name

    yield _make

    for path in created:
        path.unlink(missing_ok=True)


@pytest.fixture
def job_id():
    jid = f"test_{uuid.uuid4().hex[:8]}"
    yield jid
    shutil.rmtree(PROJECT_ROOT / "outputs" / jid, ignore_errors=True)


# ------------------------------------------------------------------ resolution

def test_upload_resolves_to_uploads_dir(upload_file, job_id):
    name = upload_file(".mp4")

    cfg = build_config_from_payload({"upload_filename": name}, job_id)

    assert cfg.file_video_asli == str(PROJECT_ROOT / "uploads" / name)
    assert os.path.isfile(cfg.file_video_asli)
    assert cfg.video_provided is True


def test_transcript_resolves_when_present(upload_file, job_id):
    video = upload_file(".mp4")
    vtt = upload_file(".vtt", "WEBVTT\n")

    cfg = build_config_from_payload(
        {"upload_filename": video, "transcript_filename": vtt}, job_id
    )

    assert cfg.transcript_path == str(PROJECT_ROOT / "uploads" / vtt)
    assert os.path.isabs(cfg.transcript_path)


def test_transcript_is_none_when_not_requested(upload_file, job_id):
    cfg = build_config_from_payload({"upload_filename": upload_file(".mp4")}, job_id)
    assert cfg.transcript_path is None


def test_missing_transcript_file_degrades_to_none(upload_file, job_id):
    """A filename with no file behind it falls back to Whisper rather than
    exploding before the worker can record an error on the job."""
    cfg = build_config_from_payload(
        {
            "upload_filename": upload_file(".mp4"),
            "transcript_filename": "never_uploaded.vtt",
        },
        job_id,
    )
    assert cfg.transcript_path is None


def test_reuse_job_points_at_job_outputs(job_id):
    """With no upload, the video is expected in the job's own outputs dir."""
    cfg = build_config_from_payload({}, job_id)

    assert cfg.file_video_asli.endswith("video_asli.mp4")
    assert job_id in cfg.file_video_asli
    assert cfg.video_provided is False


# --------------------------------------------------------------------- fields

def test_transcript_offset_and_source_url(upload_file, job_id):
    cfg = build_config_from_payload(
        {
            "upload_filename": upload_file(".mp4"),
            "transcript_offset": 3.5,
            "source_url": "Some Podcast ep.4",
        },
        job_id,
    )

    assert cfg.transcript_offset == 3.5
    assert cfg.source_url == "Some Podcast ep.4"


def test_offset_defaults_to_zero_not_none(upload_file, job_id):
    cfg = build_config_from_payload({"upload_filename": upload_file(".mp4")}, job_id)
    assert cfg.transcript_offset == 0.0


def test_ai_provider_defaults_to_nvidia(upload_file, job_id):
    cfg = build_config_from_payload({"upload_filename": upload_file(".mp4")}, job_id)
    assert cfg.ai_provider == "nvidia"


# ----------------------------------------------------------------- anti-drift

def test_adapter_supplies_every_field_resolve_transcript_reads(upload_file, job_id):
    """worker.py now calls clipping.runner.resolve_transcript with this
    namespace, so every attribute that function touches must exist here."""
    cfg = build_config_from_payload({"upload_filename": upload_file(".mp4")}, job_id)

    for attr in (
        "file_video_asli",
        "transcript_path",
        "transcript_offset",
        "no_whisper",
        "max_kata_per_subtitle",
        "whisper_model",
        "whisper_device",
        "whisper_compute_type",
        "outputs_dir",
    ):
        assert hasattr(cfg, attr), f"adapter is missing cfg.{attr}"


def test_adapter_supplies_provider_key_fields(upload_file, job_id):
    """worker.py gates on config.missing_provider_key(cfg).

    The expected env names are derived from the config tables rather than
    listed here, so adding a provider cannot leave this assertion behind.
    """
    from clipping.config import (
        PROVIDER_KEYS,
        PROVIDER_REQUIRED_EXTRA,
        missing_provider_key,
    )

    expected = {env for _, env in PROVIDER_KEYS.values()}
    for extras in PROVIDER_REQUIRED_EXTRA.values():
        expected |= {env for _, env in extras}

    cfg = build_config_from_payload({"upload_filename": upload_file(".mp4")}, job_id)

    result = missing_provider_key(cfg)
    assert result is None or result[1] in expected


# ------------------------------------------- custom OpenAI-compatible endpoint

def test_custom_endpoint_credentials_come_from_settings(upload_file, job_id):
    """The URL and key are credentials: settings/env only, never the payload."""
    cfg = build_config_from_payload(
        {"upload_filename": upload_file(".mp4"), "ai_provider": "openai_compat"},
        job_id,
        env_overrides={
            "OPENAI_COMPAT_API_KEY": "compat-key",
            "OPENAI_COMPAT_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENAI_COMPAT_MODEL": "saved-model",
        },
    )

    assert cfg.ai_provider == "openai_compat"
    assert cfg.api_key_openai_compat == "compat-key"
    assert cfg.openai_compat_base_url == "https://openrouter.ai/api/v1"
    assert cfg.openai_compat_model == "saved-model"


def test_per_job_model_overrides_the_saved_one(upload_file, job_id):
    cfg = build_config_from_payload(
        {
            "upload_filename": upload_file(".mp4"),
            "ai_provider": "openai_compat",
            "openai_compat_model": "per-job-model",
        },
        job_id,
        env_overrides={"OPENAI_COMPAT_MODEL": "saved-model"},
    )

    assert cfg.openai_compat_model == "per-job-model"


def test_empty_per_job_model_falls_back_to_the_saved_one(upload_file, job_id):
    """The field defaults to "", which must mean 'use Settings', not 'unset'."""
    cfg = build_config_from_payload(
        {
            "upload_filename": upload_file(".mp4"),
            "ai_provider": "openai_compat",
            "openai_compat_model": "",
        },
        job_id,
        env_overrides={"OPENAI_COMPAT_MODEL": "saved-model"},
    )

    assert cfg.openai_compat_model == "saved-model"


def test_half_configured_endpoint_is_caught_by_the_worker_gate(upload_file, job_id):
    """What the web worker will actually do with an incomplete endpoint."""
    from clipping.config import missing_provider_key

    cfg = build_config_from_payload(
        {"upload_filename": upload_file(".mp4"), "ai_provider": "openai_compat"},
        job_id,
        env_overrides={
            "OPENAI_COMPAT_API_KEY": "compat-key",
            "OPENAI_COMPAT_BASE_URL": "https://openrouter.ai/api/v1",
        },
    )

    assert missing_provider_key(cfg) == ("openai_compat_model", "OPENAI_COMPAT_MODEL")


def test_adapter_supplies_fields_runner_provenance_reads(upload_file, job_id):
    """run_pipeline's Step 7 reads these when stamping the manifest."""
    cfg = build_config_from_payload({"upload_filename": upload_file(".mp4")}, job_id)

    for attr in ("source_url", "file_video_asli", "whisper_model"):
        assert hasattr(cfg, attr), f"adapter is missing cfg.{attr}"


# ------------------------------------------- picking up a saved transcript

@pytest.fixture
def job_outputs():
    """Yield a factory making outputs/<job_id>/, cleaned up afterwards."""
    created: list[pathlib.Path] = []

    def _make(job_id: str) -> pathlib.Path:
        d = PROJECT_ROOT / "outputs" / job_id
        d.mkdir(parents=True, exist_ok=True)
        created.append(d)
        return d

    yield _make
    for d in created:
        shutil.rmtree(d, ignore_errors=True)


SAVED_VTT = (
    "WEBVTT\n\n00:00:01.000 --> 00:00:02.400\n"
    "<00:00:01.000>hello <00:00:01.600>world\n\n"
)


def test_a_saved_transcript_is_picked_up_from_the_jobs_own_directory(job_outputs):
    """A re-run must not redo Whisper. outputs_dir is a pure function of job_id
    and reuse_job_id reuses the id, so the re-run lands in the same directory.
    """
    job_id = "t" + uuid.uuid4().hex[:11]
    (job_outputs(job_id) / "transcript.vtt").write_text(SAVED_VTT, encoding="utf-8")

    cfg = build_config_from_payload({}, job_id)

    assert cfg.transcript_path is not None
    assert cfg.transcript_path.endswith("transcript.vtt")
    assert os.path.isfile(cfg.transcript_path)


def test_no_saved_transcript_means_whisper_still_runs(job_outputs):
    job_id = "t" + uuid.uuid4().hex[:11]
    job_outputs(job_id)
    assert build_config_from_payload({}, job_id).transcript_path is None


def test_an_uploaded_transcript_wins_over_a_saved_one(upload_file, job_outputs):
    """An explicit upload is the user saying what they want; never override it."""
    job_id = "t" + uuid.uuid4().hex[:11]
    (job_outputs(job_id) / "transcript.vtt").write_text(SAVED_VTT, encoding="utf-8")
    name = upload_file("given.vtt", SAVED_VTT)

    cfg = build_config_from_payload({"transcript_filename": name}, job_id)

    assert cfg.transcript_path.endswith(name)
    assert "outputs" not in cfg.transcript_path


def test_the_offset_is_not_applied_to_a_saved_transcript(job_outputs):
    """transcript_offset is a manual sync correction for a file the user
    supplied. A saved one was generated from this very video, so its timings are
    already right -- reapplying an old offset would desync every subtitle."""
    job_id = "t" + uuid.uuid4().hex[:11]
    (job_outputs(job_id) / "transcript.vtt").write_text(SAVED_VTT, encoding="utf-8")

    cfg = build_config_from_payload({"transcript_offset": 4.5}, job_id)

    assert cfg.transcript_path.endswith("transcript.vtt")
    assert cfg.transcript_offset == 0.0


def test_the_offset_still_applies_to_an_uploaded_transcript(upload_file):
    name = upload_file("given.vtt", SAVED_VTT)
    cfg = build_config_from_payload(
        {"transcript_filename": name, "transcript_offset": 4.5}, "job123")
    assert cfg.transcript_offset == 4.5


def test_dedupe_is_disabled_only_for_a_saved_transcript(upload_file, job_outputs):
    """The reader's dedupe drops a cue repeating the previous one -- right for
    scraped captions, wrong for speech we transcribed ourselves."""
    job_id = "t" + uuid.uuid4().hex[:11]
    (job_outputs(job_id) / "transcript.vtt").write_text(SAVED_VTT, encoding="utf-8")
    assert build_config_from_payload({}, job_id).transcript_dedupe is False

    name = upload_file("given.vtt", SAVED_VTT)
    cfg = build_config_from_payload({"transcript_filename": name}, "job456")
    assert cfg.transcript_dedupe is True
