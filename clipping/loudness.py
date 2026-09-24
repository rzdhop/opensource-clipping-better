"""
clipping.loudness — opt-in EBU R128 loudness normalisation of a finished clip.

The render mixes the hook, the voice-over and the main clip separately, and
clips from different sources arrive at whatever level they were recorded at,
so without this a batch of clips jumps in volume from one to the next.
Short-form platforms turn loud audio down but never turn quiet audio up.

Two passes, as ffmpeg's loudnorm documents for file input: the first measures
the whole file, the second applies a linear gain to reach the target without
pumping. The video stream is copied; only the audio is re-encoded.

Opt-in (``--loudnorm``), like the hook glitch: on by default it would silently
change the level of every existing script's output and add an AAC generation
to it. Best-effort: a clip that cannot be levelled is kept as rendered.

Stdlib only; the commands are built and parsed here so they are tested in the
pytest-only CI environment.
"""

from __future__ import annotations

import json
import math
import os
import subprocess

TARGET = "I=-14:TP=-1.5:LRA=11"
_MEASURED_KEYS = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")


def measure_cmd(path: str) -> list[str]:
    """First pass: measure *path*'s loudness and print it as JSON."""
    return [
        "ffmpeg", "-hide_banner", "-nostats", "-i", path, "-vn",
        "-af", f"loudnorm={TARGET}:print_format=json",
        "-f", "null", "-",
    ]


def parse_measurement(stderr: str) -> dict | None:
    """The first pass's measurement, or None when it is missing or unusable.

    A silent track measures ``-inf``, which the second pass cannot apply, so a
    value that is not a finite number counts as no measurement.
    """
    at = stderr.find("[Parsed_loudnorm")
    if at < 0:
        return None
    start = stderr.find("{", at)
    end = stderr.find("}", start)
    if start < 0 or end < 0:
        return None
    try:
        data = json.loads(stderr[start:end + 1])
        measured = {key: str(data[key]) for key in _MEASURED_KEYS}
        if not all(math.isfinite(float(value)) for value in measured.values()):
            return None
    except (ValueError, KeyError, TypeError):
        return None
    return measured


def apply_cmd(src: str, dst: str, measured: dict, sample_rate: int) -> list[str]:
    """Second pass: apply the measured correction, copying the video."""
    af = (
        f"loudnorm={TARGET}"
        f":measured_I={measured['input_i']}"
        f":measured_TP={measured['input_tp']}"
        f":measured_LRA={measured['input_lra']}"
        f":measured_thresh={measured['input_thresh']}"
        f":offset={measured['target_offset']}"
        ":linear=true:print_format=summary"
    )
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", src,
        "-map", "0", "-c:v", "copy", "-af", af,
        # loudnorm resamples to 192 kHz internally; put the source rate back.
        "-c:a", "aac", "-b:a", "192k", "-ar", str(sample_rate),
        "-movflags", "+faststart", dst,
    ]


def probe_sample_rate(path: str, *, run=subprocess.run, default: int = 48000) -> int:
    """The first audio stream's sample rate, or *default*."""
    try:
        result = run(
            ["ffprobe", "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=sample_rate", "-of", "default=nw=1:nk=1", path],
            capture_output=True, text=True,
        )
        return int(result.stdout.strip().splitlines()[0])
    except (ValueError, IndexError, OSError):
        return default


def normalize_file(path: str, *, run=subprocess.run, on_log=print) -> bool:
    """Level *path* to -14 LUFS in place. Returns False, and leaves the file as
    rendered, if it cannot."""
    name = os.path.basename(path)
    tmp = path + ".loudnorm.mp4"
    try:
        first = run(measure_cmd(path), capture_output=True, text=True)
        measured = parse_measurement(first.stderr) if first.returncode == 0 else None
        if measured is None:
            on_log(f"   ⚠️ [Loudness] Could not measure {name}; kept as rendered.")
            return False

        second = run(apply_cmd(path, tmp, measured, probe_sample_rate(path, run=run)),
                     capture_output=True, text=True)
        if second.returncode != 0 or not os.path.isfile(tmp) or os.path.getsize(tmp) == 0:
            tail = (second.stderr or "").strip()[-200:]
            on_log(f"   ⚠️ [Loudness] Levelling {name} failed; kept as rendered. {tail}")
            return False

        os.replace(tmp, path)
        on_log(f"   🔊 [Loudness] {name}: {measured['input_i']} LUFS -> -14 LUFS")
        return True
    except Exception as exc:  # noqa: BLE001 - best-effort by design
        on_log(f"   ⚠️ [Loudness] {name}: {type(exc).__name__}: {exc}; kept as rendered.")
        return False
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)
