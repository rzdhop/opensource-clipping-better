"""What Claude can look at: a downscaled image, a contact sheet of a clip,
the duration and shape of a voice line.

A tool answer travels inside the chat, so an image is sent small (JPEG,
``max_px`` on its longer side) and a clip as one sheet of evenly spaced
frames with its duration, size and whether it carries sound. Pillow for the
images, ``ffmpeg``/``ffprobe`` for the clips (both already required by the
renderer).
"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile

IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
VIDEO_EXTS = (".mp4", ".mov", ".webm", ".mkv")
AUDIO_EXTS = (".wav", ".flac", ".mp3", ".ogg", ".m4a")
DEFAULT_MAX_PX = 1024
SHEET_COLS, SHEET_ROWS = 4, 2
SHEET_TILE_W = 320


class MediaError(Exception):
    pass


def is_image(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in IMAGE_EXTS


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTS


def is_audio(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in AUDIO_EXTS


def _ffmpeg(name: str) -> str:
    binary = shutil.which(name)
    if not binary:
        raise MediaError(f"{name} is not installed on the server")
    return binary


def thumbnail(path: str, max_px: int = DEFAULT_MAX_PX) -> tuple:
    """``(jpeg_bytes, width, height)`` of *path* scaled to fit *max_px*."""
    from PIL import Image

    with Image.open(path) as im:
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue(), im.width, im.height


def _probe(path: str) -> dict:
    out = subprocess.run(
        [_ffmpeg("ffprobe"), "-v", "error", "-print_format", "json", "-show_streams", "-show_format", path],
        capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise MediaError(f"ffprobe could not read {path}: {out.stderr.strip()[:200]}")
    return json.loads(out.stdout or "{}")


def probe_audio(path: str) -> dict:
    """Duration, sample rate, channels and codec of a sound file (``ffprobe``)."""
    info = _probe(path)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if audio is None:
        raise MediaError(f"{path} has no audio stream")
    duration = float((info.get("format") or {}).get("duration") or audio.get("duration") or 0)
    return {"duration_s": round(duration, 3), "sample_rate": int(audio.get("sample_rate") or 0),
            "channels": int(audio.get("channels") or 0), "audio_codec": audio.get("codec_name"),
            "size_bytes": os.path.getsize(path)}


def probe_video(path: str) -> dict:
    """Duration, frame size, fps and sound of a clip (``ffprobe``)."""
    info = _probe(path)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    audio = next((s for s in info.get("streams", []) if s.get("codec_type") == "audio"), None)
    if video is None:
        raise MediaError(f"{path} has no video stream")
    num, _, den = (video.get("avg_frame_rate") or "0/1").partition("/")
    fps = round(float(num) / float(den or 1), 3) if float(den or 1) else 0.0
    duration = float((info.get("format") or {}).get("duration") or video.get("duration") or 0)
    return {"duration_s": round(duration, 3), "width": int(video.get("width") or 0),
            "height": int(video.get("height") or 0), "fps": fps, "has_audio": audio is not None,
            "audio_codec": (audio or {}).get("codec_name"), "size_bytes": os.path.getsize(path)}


def contact_sheet(path: str, *, cols: int = SHEET_COLS, rows: int = SHEET_ROWS, tile_w: int = SHEET_TILE_W,
                  probe: dict | None = None) -> bytes:
    """One JPEG of ``cols x rows`` frames taken evenly across the clip, in order."""
    info = probe or probe_video(path)
    count = max(1, cols * rows)
    duration = max(info["duration_s"], 0.001)
    # One frame every duration/count seconds, starting half a step in, so the sheet spans the clip.
    step = duration / count
    with tempfile.TemporaryDirectory() as tmp:
        out_path = os.path.join(tmp, "sheet.jpg")
        vf = (f"fps=1/{step:.6f},scale={tile_w}:-2,"
              f"tile={cols}x{rows}:padding=2:margin=2:color=black")
        cmd = [_ffmpeg("ffmpeg"), "-v", "error", "-y", "-ss", f"{step / 2:.6f}", "-i", path, "-vf", vf,
               "-frames:v", "1", "-q:v", "4", out_path]
        out = subprocess.run(cmd, capture_output=True, text=True, check=False)
        if out.returncode != 0 or not os.path.isfile(out_path):
            raise MediaError(f"ffmpeg could not build the contact sheet: {out.stderr.strip()[:200]}")
        with open(out_path, "rb") as fh:
            return fh.read()
