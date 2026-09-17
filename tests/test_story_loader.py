"""Tests for the local-only story sources schema."""

import json

import pytest

from clipping.story import loader


def write_sources(tmp_path, sources):
    path = tmp_path / "sources.json"
    path.write_text(
        json.dumps({"$schema": "sources_v1", "sources": sources}),
        encoding="utf-8",
    )
    return str(path)


@pytest.fixture
def media(tmp_path):
    video = tmp_path / "a.mp4"
    video.write_bytes(b"\x00")
    vtt = tmp_path / "a.vtt"
    vtt.write_text(
        "WEBVTT\n\n00:00:01.000 --> 00:00:03.000\nhello\n", encoding="utf-8"
    )
    return video, vtt


def test_local_source_loads(tmp_path, media):
    video, vtt = media
    path = write_sources(tmp_path, [
        {"id": "a", "name": "A", "platform": "local",
         "local_path": str(video), "transcript_path": str(vtt)}
    ])

    registry = loader.load_sources(path)

    assert set(registry) == {"a"}
    assert registry["a"]["transcript_path"] == str(vtt)


def test_transcript_path_is_optional(tmp_path, media):
    video, _ = media
    path = write_sources(tmp_path, [
        {"id": "a", "name": "A", "platform": "local", "local_path": str(video)}
    ])

    registry = loader.load_sources(path)
    assert registry["a"].get("transcript_path") is None


def test_origin_url_is_ignored_not_rejected(tmp_path, media):
    """Attribution is allowed to ride along; it is never fetched."""
    video, _ = media
    path = write_sources(tmp_path, [
        {"id": "a", "name": "A", "platform": "local", "local_path": str(video),
         "origin_url": "https://example.com/original"}
    ])

    registry = loader.load_sources(path)
    assert registry["a"]["origin_url"] == "https://example.com/original"


# ------------------------------------------------------------------ failures

@pytest.mark.parametrize("platform", ["youtube", "tiktok", "instagram", "gdrive"])
def test_remote_platform_rejected_with_migration_guidance(tmp_path, platform):
    path = write_sources(tmp_path, [
        {"id": "legacy_one", "name": "L", "platform": platform,
         "url": "https://example.com/x"}
    ])

    with pytest.raises(ValueError) as excinfo:
        loader.load_sources(path)

    message = str(excinfo.value)
    # The error must name the offending source and explain the migration.
    assert "legacy_one" in message
    assert "local" in message
    assert "local_path" in message
    assert "transcript_path" in message


def test_missing_local_path_rejected(tmp_path):
    path = write_sources(tmp_path, [{"id": "a", "name": "A", "platform": "local"}])

    with pytest.raises(ValueError, match="local_path"):
        loader.load_sources(path)


def test_nonexistent_local_path_rejected(tmp_path):
    path = write_sources(tmp_path, [
        {"id": "a", "name": "A", "platform": "local", "local_path": "nope.mp4"}
    ])

    with pytest.raises(ValueError, match="local_path not found"):
        loader.load_sources(path)


def test_nonexistent_transcript_rejected(tmp_path, media):
    """Fails here rather than after a Whisper model has loaded."""
    video, _ = media
    path = write_sources(tmp_path, [
        {"id": "a", "name": "A", "platform": "local",
         "local_path": str(video), "transcript_path": "nope.vtt"}
    ])

    with pytest.raises(ValueError, match="transcript_path"):
        loader.load_sources(path)


def test_bad_transcript_extension_rejected(tmp_path, media):
    video, _ = media
    bad = tmp_path / "a.txt"
    bad.write_text("x", encoding="utf-8")
    path = write_sources(tmp_path, [
        {"id": "a", "name": "A", "platform": "local",
         "local_path": str(video), "transcript_path": str(bad)}
    ])

    with pytest.raises(ValueError, match="unsupported transcript_path format"):
        loader.load_sources(path)


def test_duplicate_ids_rejected(tmp_path, media):
    video, _ = media
    entry = {"id": "a", "name": "A", "platform": "local", "local_path": str(video)}
    path = write_sources(tmp_path, [entry, dict(entry)])

    with pytest.raises(ValueError, match="Duplicate"):
        loader.load_sources(path)


# ------------------------------------------------------------- shipped files

@pytest.mark.parametrize(
    "path", ["sources.json", "example/story/sources.sample.json"]
)
def test_shipped_sources_use_the_local_schema(path):
    """The files we ship must not demonstrate a schema the loader rejects."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    doc = json.loads((root / path).read_text(encoding="utf-8"))

    for src in doc["sources"]:
        assert src["platform"] == "local", src["id"]
        assert "local_path" in src, src["id"]
        assert "url" not in src, src["id"]
