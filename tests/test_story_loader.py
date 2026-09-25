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


def test_the_source_manager_does_not_claim_to_download():
    """Its docstring still promised downloads from YouTube, TikTok, Instagram
    and Google Drive after the fetch code was deleted; the loader it sits beside
    already says sources are local files only."""
    import ast
    import pathlib

    path = pathlib.Path(__file__).resolve().parents[1] / "clipping" / "story" / "source_manager.py"
    source = path.read_text(encoding="utf-8")
    doc = ast.get_docstring(ast.parse(source)) or ""
    for platform in ("YouTube", "TikTok", "Instagram", "Google Drive"):
        assert platform not in doc, platform
    assert "yt_dlp" not in doc
    assert "local" in doc.lower()
    assert "SINGLE SOURCE DOWNLOAD" not in source


# ------------------------------------------------------------------ the label

def test_the_legacy_mode_is_labelled_story_clip_assembly():
    """DEC-095: the multi-source assembly behind `--story-mode` is called
    "Story Clip (assembly)" wherever a user sees it, so it is never confused
    with the dashboard's AI Story mode. The flag and its behaviour do not change."""
    from clipping import config

    parser = config._build_parser()
    titles = [group.title for group in parser._action_groups]
    assert "Story Clip (assembly)" in titles
    assert "Story Clip Mode" not in titles
    flag = next(a for a in parser._actions if "--story-mode" in a.option_strings)
    assert flag.default is False
    assert "assembly" in flag.help


def test_no_user_facing_file_still_says_story_clip_mode():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    offenders = []
    for rel in ("main.py", "clipping/config.py", "clipping/story_runner.py",
                "README.md", "README_ID.md", "docs/STORY_CLIP.md", "docs/STORY_CLIP_ID.md",
                "wiki/1-Home.md", "wiki/3-CLI-Reference.md", "wiki/10-Story-Clip-Mode.md",
                "wiki/14-Contributing.md", "wiki/_Sidebar.md"):
        for line_no, line in enumerate((root / rel).read_text(encoding="utf-8").splitlines(), 1):
            if "Story Clip Mode" in line or "Mode Story Clip" in line or "Story mode modules" in line:
                offenders.append(f"{rel}:{line_no}: {line.strip()[:70]}")
    assert offenders == [], "\n".join(offenders)
