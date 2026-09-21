"""Write a .srt beside each rendered clip.

The burned-in subtitles are part of the video and cannot be turned off, edited
or translated afterwards. A sidecar file can: it is what an editor imports, what
a platform's own captioning accepts, and what someone re-times or translates
without re-rendering.

Built from ``data_segmen`` rather than from the generated ASS. The ASS carries
karaoke tags, per-word colour changes and positioning that mean nothing in SRT,
and reading it back would couple this to the render layer's internals — the
layer with zero automated coverage that everything else here is careful not to
touch. ``data_segmen`` is the same source the ASS was built from.

Stdlib only.
"""

from __future__ import annotations

import os

from . import transcript as transcript_mod


def clip_srt_name(rank):
    """``highlight_rank_2.srt`` — matches the clip's own naming."""
    return f"highlight_rank_{rank}.srt"


def export_clip_srt(data_segmen, entry, outputs_dir, *, on_log=None):
    """Write the SRT for one rendered clip. Returns its path, or None.

    Never raises. The clip is already rendered by the time this runs, and a
    disk error must not turn a finished job into a failed one over a sidecar
    file — the same reasoning as the transcript writer in ``runner.py``.
    """
    try:
        rank = entry["rank"]
        start = float(entry["start_time"])
        end = float(entry["end_time"])
    except (KeyError, TypeError, ValueError):
        return None

    if end <= start:
        return None

    path = os.path.join(outputs_dir, clip_srt_name(rank))
    try:
        transcript_mod.write_srt(
            data_segmen, path, clip_start=start, clip_end=end
        )
    except OSError as exc:
        if on_log:
            on_log(f"   ⚠️ Could not write {os.path.basename(path)} ({exc}).")
        return None

    # An empty file means the clip has no speech in it; a player shows nothing
    # either way, but leaving a zero-byte file looks like a bug.
    if os.path.getsize(path) == 0:
        try:
            os.unlink(path)
        except OSError:
            pass
        return None

    return path


def export_all(data_segmen, manifest, outputs_dir, *, on_log=None):
    """Write an SRT for every entry in *manifest*, recording ``srt_path``.

    Returns how many were written. Mutates the manifest entries, which is what
    puts ``srt_path`` into ``render_manifest.json`` and from there in front of
    anything that reads it.
    """
    if not data_segmen or not manifest:
        return 0

    written = 0
    for entry in manifest:
        if not isinstance(entry, dict):
            continue
        path = export_clip_srt(data_segmen, entry, outputs_dir, on_log=on_log)
        if path:
            entry["srt_path"] = path
            written += 1

    if written and on_log:
        on_log(f"   💬 Wrote {written} subtitle file(s) (.srt) beside the clips.")
    return written
