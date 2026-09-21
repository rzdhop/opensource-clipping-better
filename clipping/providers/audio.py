"""Extract and chunk audio for a hosted transcription API.

Hosted speech-to-text has a file-size ceiling — 25 MB on Groq's free tier — and
that ceiling binds well before any duration limit does. Measured on the real
20-minute video this was built for: **39.3 MB** at 16 kHz mono FLAC, half again
over the cap. FLAC is variable-rate, so a quiet interview of the same length
would fit; that is why the plan is derived from the file's own bytes-per-second
rather than from its duration.

So audio is always extracted to the smallest faithful form (16 kHz mono FLAC,
which is what every Whisper-family model resamples to anyway) and split when it
would not fit, on **silence** rather than on a clock, so no chunk boundary falls
mid-word.

ffmpeg and ffprobe are invoked as subprocesses. Stdlib only.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile

# Groq's free tier caps uploads at 25 MB. Aim under it with room for container
# overhead, because being 2% over costs a whole retry.
DEFAULT_MAX_BYTES = 24 * 1024 * 1024
DEFAULT_CHUNK_SECONDS = 600.0
# Chunks overlap so a word spanning a boundary is transcribed whole by at least
# one of them; the duplicate is removed when they are stitched back together.
DEFAULT_OVERLAP = 4.0
# How far a boundary may move to land in silence.
SILENCE_SEARCH_WINDOW = 15.0
# A trailing chunk shorter than this is folded into the one before it when
# there is room. Measured on the real 20-minute video: the arithmetic left a
# 10.8-second tail, which is its own upload, its own round trip, and its own
# seam to stitch -- and Groq bills a 10-second minimum per request regardless.
MIN_TAIL_SECONDS = 30.0

_SILENCE_RE = re.compile(r"silence_(start|end):\s*(-?\d+(?:\.\d+)?)")


class AudioError(RuntimeError):
    """ffmpeg or ffprobe could not do what was asked."""


def _run(args, *, capture_stderr=False):
    try:
        proc = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError as exc:
        raise AudioError(f"{args[0]} is not installed or not on PATH.") from exc
    if proc.returncode != 0 and not capture_stderr:
        tail = proc.stderr.decode("utf-8", "replace")[-400:]
        raise AudioError(f"{args[0]} failed: {tail}")
    return proc


def probe_duration(path):
    """Length of *path* in seconds."""
    proc = _run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=nw=1:nk=1", path,
    ])
    text = proc.stdout.decode("utf-8", "replace").strip()
    try:
        return float(text)
    except ValueError as exc:
        raise AudioError(f"ffprobe gave no duration for {path}: {text!r}") from exc


def extract(video_path, out_path=None):
    """Write *video_path*'s audio as 16 kHz mono FLAC and return the path.

    16 kHz mono because every Whisper-family model resamples to exactly that;
    sending more is paying upload time for data the model discards. FLAC because
    it is lossless at roughly half the size of WAV, so nothing is lost to
    compression before the model that matters sees it.
    """
    if out_path is None:
        handle, out_path = tempfile.mkstemp(suffix=".flac", prefix="rzclips_audio_")
        os.close(handle)

    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-i", video_path,
        "-vn", "-ac", "1", "-ar", "16000",
        "-c:a", "flac", "-compression_level", "8",
        out_path,
    ])
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise AudioError(f"ffmpeg produced no audio for {video_path}.")
    return out_path


def find_silences(path, *, noise_db=-35, min_duration=0.4):
    """Midpoints of the silent stretches in *path*, in seconds."""
    proc = _run(
        [
            "ffmpeg", "-hide_banner", "-nostats", "-i", path,
            "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
            "-f", "null", "-",
        ],
        capture_stderr=True,
    )
    text = proc.stderr.decode("utf-8", "replace")

    midpoints = []
    start = None
    for kind, value in _SILENCE_RE.findall(text):
        seconds = float(value)
        if kind == "start":
            start = seconds
        elif start is not None:
            midpoints.append((start + seconds) / 2.0)
            start = None
    return midpoints


def plan_chunks(
    path,
    *,
    duration=None,
    size_bytes=None,
    max_bytes=DEFAULT_MAX_BYTES,
    target_seconds=DEFAULT_CHUNK_SECONDS,
    overlap=DEFAULT_OVERLAP,
    silences=None,
):
    """``[(start, end), ...]`` covering *path*, each small enough to upload.

    The bytes-per-second rate is derived from the **actual** file rather than
    assumed from the format: FLAC is variable-rate, and a quiet interview
    compresses to a fraction of what a music-heavy video does. Assuming a rate
    would mean either splitting files that never needed it or shipping chunks
    that get rejected.

    Boundaries are nudged to the nearest silence within
    ``SILENCE_SEARCH_WINDOW``, so a cut does not land mid-word.
    """
    duration = probe_duration(path) if duration is None else float(duration)
    size_bytes = os.path.getsize(path) if size_bytes is None else int(size_bytes)

    if duration <= 0:
        raise AudioError(f"{path} has no duration.")

    if size_bytes <= max_bytes:
        return [(0.0, duration)]

    bytes_per_second = size_bytes / duration
    seconds_that_fit = max_bytes / bytes_per_second if bytes_per_second else target_seconds
    # A 10% margin: the rate is an average, and a loud passage exceeds it.
    chunk = max(30.0, min(float(target_seconds), seconds_that_fit * 0.9))

    if silences is None:
        try:
            silences = find_silences(path)
        except AudioError:
            silences = []

    bounds = []
    start = 0.0
    while start < duration - 0.01:
        end = min(duration, start + chunk)
        if end < duration:
            end = _snap_to_silence(end, silences, start + 30.0, duration)
        bounds.append((start, end))
        if end >= duration:
            break
        start = max(0.0, end - overlap)

    return _absorb_short_tail(bounds, max_bytes, bytes_per_second)


def _absorb_short_tail(bounds, max_bytes, bytes_per_second):
    """Fold a stub final chunk into its predecessor when the result still fits."""
    if len(bounds) < 2:
        return bounds

    last_start, last_end = bounds[-1]
    if last_end - last_start >= MIN_TAIL_SECONDS:
        return bounds

    prev_start, _prev_end = bounds[-2]
    merged_seconds = last_end - prev_start
    if bytes_per_second and merged_seconds * bytes_per_second > max_bytes:
        return bounds  # it would not fit; a short chunk beats a rejected one

    return bounds[:-2] + [(prev_start, last_end)]


def _snap_to_silence(target, silences, floor, ceiling):
    """Move *target* to the nearest silence within the search window."""
    best = None
    for point in silences:
        if point <= floor or point >= ceiling:
            continue
        if abs(point - target) <= SILENCE_SEARCH_WINDOW:
            if best is None or abs(point - target) < abs(best - target):
                best = point
    return best if best is not None else target


def cut(path, start, end, out_path=None):
    """Write ``[start, end)`` of *path* to a new file and return its path.

    Stream copy: re-encoding here would cost minutes and change nothing the
    transcription model can hear.
    """
    if out_path is None:
        handle, out_path = tempfile.mkstemp(suffix=".flac", prefix="rzclips_chunk_")
        os.close(handle)

    _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{start:.3f}", "-to", f"{end:.3f}",
        "-i", path, "-c", "copy", out_path,
    ])
    if not os.path.isfile(out_path) or os.path.getsize(out_path) == 0:
        raise AudioError(f"ffmpeg produced an empty chunk for {start}-{end}.")
    return out_path
