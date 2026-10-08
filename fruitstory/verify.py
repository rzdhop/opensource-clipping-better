"""Quick checks of a generated clip, with ffmpeg/ffprobe only (no model, no GPU).

- :func:`probe` -> duration, size, fps, whether an audio stream exists, its sample rate.
- :func:`loudness` -> mean/max volume of the audio (``volumedetect``), to catch silent takes.
- :func:`contact_sheet` -> one jpg of N frames spread over the clip, to look at in the chat.
- :func:`extract_audio` -> the clip's sound as a mono 24 kHz wav (a voice reference, or the
  input of an STT check later).

The STT alignment of the spoken line (plan 36 §2.2) comes in stage 1; this module is what
stage 0 needs to review takes by eye and ear.
"""

from __future__ import annotations

import json
import os
import re
import subprocess


def _run(cmd: list, *, capture: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=capture, text=True, check=False)


def probe(path: str) -> dict:
    out = _run(["ffprobe", "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path])
    if out.returncode:
        raise RuntimeError(f"ffprobe failed on {path}: {out.stderr.strip()[:300]}")
    info = json.loads(out.stdout)
    video = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
    audio = next((s for s in info["streams"] if s.get("codec_type") == "audio"), None)
    fps = None
    if video and video.get("avg_frame_rate", "0/0") not in ("0/0", "0"):
        num, den = video["avg_frame_rate"].split("/")
        fps = round(int(num) / max(int(den), 1), 3)
    return {
        "duration_s": round(float(info["format"].get("duration") or 0), 3),
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "fps": fps,
        "frames": int(video.get("nb_frames") or 0) if video else 0,
        "has_audio": audio is not None,
        "sample_rate": int(audio["sample_rate"]) if audio else None,
        "size_mb": round(os.path.getsize(path) / 1e6, 2),
    }


def loudness(path: str) -> dict:
    out = _run(["ffmpeg", "-hide_banner", "-i", path, "-vn", "-af", "volumedetect", "-f", "null", "-"])
    text = out.stderr
    mean = re.search(r"mean_volume:\s*(-?[\d.]+) dB", text)
    peak = re.search(r"max_volume:\s*(-?[\d.]+) dB", text)
    return {"mean_db": float(mean.group(1)) if mean else None, "max_db": float(peak.group(1)) if peak else None,
            "silent": (not mean) or float(mean.group(1)) < -50}


def contact_sheet(path: str, dest: str, *, frames: int = 8, width: int = 270) -> str:
    """*frames* frames spread evenly over the clip, tiled in one row, written to *dest* (jpg)."""
    info = probe(path)
    total = max(info["frames"], 1)
    step = max(total // frames, 1)
    vf = f"select='not(mod(n\\,{step}))',scale={width}:-2,tile={frames}x1"
    out = _run(["ffmpeg", "-hide_banner", "-y", "-i", path, "-vf", vf, "-frames:v", "1", "-q:v", "4", dest])
    if out.returncode:
        raise RuntimeError(f"contact sheet failed: {out.stderr.strip()[-300:]}")
    return dest


def extract_audio(path: str, dest: str, *, start_s: float = 0.0, max_s: float | None = None,
                  sample_rate: int = 24000) -> str:
    """The clip's audio as mono 16-bit wav at *sample_rate* (what Chatterbox and LTX's
    reference-audio node expect)."""
    cmd = ["ffmpeg", "-hide_banner", "-y", "-i", path, "-vn", "-ac", "1", "-ar", str(sample_rate),
           "-acodec", "pcm_s16le"]
    if start_s:
        cmd += ["-ss", str(start_s)]
    if max_s:
        cmd += ["-t", str(max_s)]
    out = _run(cmd + [dest])
    if out.returncode:
        raise RuntimeError(f"audio extraction failed: {out.stderr.strip()[-300:]}")
    return dest


def concat_audio(parts: list, dest: str, *, gap_s: float = 0.35, sample_rate: int = 24000) -> str:
    """Join wav *parts* with *gap_s* of silence between them (a two-speaker exchange for A2V)."""
    inputs = []
    filters = []
    for k, p in enumerate(parts):
        inputs += ["-i", p]
        filters.append(f"[{k}:a]aresample={sample_rate},aformat=channel_layouts=mono,apad=pad_dur={gap_s}[a{k}]")
    chain = "".join(f"[a{k}]" for k in range(len(parts)))
    graph = ";".join(filters) + f";{chain}concat=n={len(parts)}:v=0:a=1[out]"
    out = _run(["ffmpeg", "-hide_banner", "-y", *inputs, "-filter_complex", graph, "-map", "[out]",
                "-ar", str(sample_rate), "-ac", "1", "-acodec", "pcm_s16le", dest])
    if out.returncode:
        raise RuntimeError(f"audio concat failed: {out.stderr.strip()[-300:]}")
    return dest


def report(path: str) -> dict:
    info = probe(path)
    info.update(loudness(path) if info["has_audio"] else {"mean_db": None, "max_db": None, "silent": True})
    return info
