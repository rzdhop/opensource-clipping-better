"""A .srt beside each rendered clip.

The burned-in subtitles are part of the video: they cannot be turned off,
edited or translated afterwards. A sidecar can be. Built from data_segmen
rather than from the generated ASS, because the ASS carries karaoke tags and
positioning that mean nothing in SRT, and reading it back would couple this to
the render layer's internals.

Stdlib only.
"""

import os

import pytest

from clipping import subtitles_export, transcript


def words(text, start=0.0, step=0.5):
    return [
        {"word": w, "start": start + i * step, "end": start + (i + 1) * step - 0.01}
        for i, w in enumerate(text.split())
    ]


@pytest.fixture
def segmen():
    """Three seconds of speech, then a gap, then three more."""
    first = words("one two three four", start=0.0)
    second = words("five six seven eight", start=10.0)
    return [
        {"start": first[0]["start"], "end": first[-1]["end"], "words": first},
        {"start": second[0]["start"], "end": second[-1]["end"], "words": second},
    ]


def entry(rank=1, start=0.0, end=2.0):
    return {"rank": rank, "start_time": start, "end_time": end,
            "video_path": f"/out/highlight_rank_{rank}_ready.mp4"}


# ------------------------------------------------------------------ one clip

def test_a_clip_gets_a_file_named_after_it(segmen, tmp_path):
    path = subtitles_export.export_clip_srt(segmen, entry(2, 0.0, 2.0), str(tmp_path))
    assert os.path.basename(path) == "highlight_rank_2.srt"
    assert os.path.isfile(path)


def test_the_file_is_rebased_to_the_clip(segmen, tmp_path):
    """A subtitle file shipped next to a clip has to start at zero; data_segmen
    is always source-absolute."""
    path = subtitles_export.export_clip_srt(segmen, entry(1, 10.0, 12.0), str(tmp_path))
    text = open(path, encoding="utf-8").read()
    assert "00:00:00,000" in text
    assert "00:00:10" not in text


def test_only_the_words_inside_the_clip_are_written(segmen, tmp_path):
    path = subtitles_export.export_clip_srt(segmen, entry(1, 10.0, 12.0), str(tmp_path))
    text = open(path, encoding="utf-8").read()
    assert "five" in text
    assert "one" not in text and "two" not in text


def test_a_clip_with_no_speech_writes_nothing(segmen, tmp_path):
    """A zero-byte file looks like a bug; a player shows nothing either way."""
    assert subtitles_export.export_clip_srt(
        segmen, entry(1, 500.0, 530.0), str(tmp_path)
    ) is None
    assert list(tmp_path.iterdir()) == []


# -------------------------------------------------------------- the manifest

def test_every_clip_is_exported_and_recorded(segmen, tmp_path):
    manifest = [entry(1, 0.0, 2.0), entry(2, 10.0, 12.0)]
    written = subtitles_export.export_all(segmen, manifest, str(tmp_path))

    assert written == 2
    for item in manifest:
        assert os.path.isfile(item["srt_path"])


def test_srt_path_lands_in_the_manifest(segmen, tmp_path):
    """render_manifest.json is the only cross-process contract for a finished
    clip; a path not written there is invisible to everything downstream."""
    manifest = [entry(1, 0.0, 2.0)]
    subtitles_export.export_all(segmen, manifest, str(tmp_path))
    assert manifest[0]["srt_path"].endswith("highlight_rank_1.srt")


def test_a_clip_with_no_speech_gets_no_srt_path(segmen, tmp_path):
    manifest = [entry(1, 0.0, 2.0), entry(2, 900.0, 930.0)]
    subtitles_export.export_all(segmen, manifest, str(tmp_path))
    assert "srt_path" in manifest[0]
    assert "srt_path" not in manifest[1]


def test_no_transcript_means_no_files(tmp_path):
    manifest = [entry(1, 0.0, 2.0)]
    assert subtitles_export.export_all([], manifest, str(tmp_path)) == 0
    assert "srt_path" not in manifest[0]


# -------------------------------------------------------------- failure modes

def test_a_malformed_entry_is_skipped_not_fatal(segmen, tmp_path):
    """The clips are already rendered by the time this runs. A sidecar must
    never turn a finished job into a failed one."""
    manifest = [
        {"rank": 1},                                   # no times
        {"rank": 2, "start_time": "x", "end_time": 3},  # not numbers
        {"start_time": 0.0, "end_time": 2.0},           # no rank
        entry(4, 0.0, 2.0),                             # fine
    ]
    assert subtitles_export.export_all(segmen, manifest, str(tmp_path)) == 1


def test_a_degenerate_span_is_skipped(segmen, tmp_path):
    assert subtitles_export.export_clip_srt(
        segmen, entry(1, 5.0, 5.0), str(tmp_path)
    ) is None


def test_an_unwritable_directory_is_reported_not_raised(segmen, tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    blocked.chmod(0o500)
    logs = []
    try:
        result = subtitles_export.export_clip_srt(
            segmen, entry(1, 0.0, 2.0), str(blocked), on_log=logs.append
        )
        assert result is None
        assert logs and "Could not write" in logs[0]
    finally:
        blocked.chmod(0o700)


def test_non_dict_manifest_entries_are_ignored(segmen, tmp_path):
    assert subtitles_export.export_all(
        segmen, ["not a dict", None, entry(1, 0.0, 2.0)], str(tmp_path)
    ) == 1


# ------------------------------------------------------------- readable back

def test_the_result_parses_as_a_subtitle_file(segmen, tmp_path):
    path = subtitles_export.export_clip_srt(segmen, entry(1, 0.0, 2.5), str(tmp_path))
    _, reread = transcript.parse_vtt_subs(path, dedupe=False)

    assert reread
    assert [w["word"] for s in reread for w in s["words"]][:2] == ["one", "two"]


def test_the_server_can_serve_it():
    """.srt is in the media-type map, or the download link 404s."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1]
           / "web" / "api" / "routes" / "files.py").read_text(encoding="utf-8")
    assert '".srt"' in src


# ---------------------------------------------------------------- the wiring

@pytest.mark.parametrize("module", ["clipping/runner.py", "web/api/worker.py"])
def test_both_render_loops_export(module):
    """The CLI and the web path duplicate the render loop; a step added to one
    and not the other is the classic drift in this codebase."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / module).read_text(
        encoding="utf-8"
    )
    assert "subtitles_export" in src, module


@pytest.mark.parametrize("module", ["clipping/runner.py", "web/api/worker.py"])
def test_the_export_happens_before_the_manifest_is_saved(module):
    """srt_path is written onto the manifest entries, so exporting afterwards
    would record nothing."""
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / module).read_text(
        encoding="utf-8"
    )
    assert src.index("subtitles_export.export_all") < src.index(
        'json.dump(render_manifest'
    ), module
