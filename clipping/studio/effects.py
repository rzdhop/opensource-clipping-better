import html
import importlib.util
import json
import math
import os
import random
import re
import shutil
import string
import subprocess
import textwrap
import time
import urllib.parse
import urllib.request

import cv2
import mediapipe as mp
import numpy as np
import requests
from mediapipe.tasks import python as mp_python
from mediapipe.tasks.python import vision as mp_vision
from PIL import Image, ImageDraw, ImageFont
from yt_dlp import YoutubeDL

FIREFOX_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:148.0) Gecko/20100101 Firefox/148.0"

def _load_studio_internal_module(file_name: str, module_alias: str):
    module_path = os.path.join(os.path.dirname(__file__), file_name)
    spec = importlib.util.spec_from_file_location(module_alias, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

_helpers = _load_studio_internal_module("helpers.py", "clipping_studio_helpers")
_ffmpeg_utils = _load_studio_internal_module("ffmpeg_utils.py", "clipping_studio_ffmpeg_utils")
format_seconds = _helpers.format_seconds
escape_ffmpeg_filter_value = _helpers.escape_ffmpeg_filter_value
detect_video_encoder = _ffmpeg_utils.detect_video_encoder
get_ts_encode_args = _ffmpeg_utils.get_ts_encode_args
get_mp4_encode_args = _ffmpeg_utils.get_mp4_encode_args
open_ffmpeg_video_writer = _ffmpeg_utils.open_ffmpeg_video_writer
build_ffmpeg_progress_cmd = _ffmpeg_utils.build_ffmpeg_progress_cmd
run_ffmpeg_with_progress = _ffmpeg_utils.run_ffmpeg_with_progress

utils = _load_studio_internal_module("utils.py", "clipping_studio_utils")
_get_render_dims = utils._get_render_dims
_is_vertical_ratio = utils._is_vertical_ratio
RATIO_MAP = utils.RATIO_MAP

# v1 built the noise on a pure black base and rendered at 19/255 mean luma -- a
# black frame a user reported as a bug. v2 uses a mid-grey base (~126/255).
GLITCH_RECIPE_VERSION = 2


def siapkan_glitch_video(rasio, cfg, video_encoder, source_h=1080, custom_dims=None):
    """
    Generate a 1-second VHS glitch transition video.

    Primary: download glitch video from cfg.url_glitch_video via yt_dlp.
    Fallback: generate RGB-shift noise via FFmpeg lavfi filters if download fails.

    Args:
        rasio (str): Target output ratio string ('9:16', '16:9', etc.).
        cfg: Runtime config (used for render_output_height, video_scale_algo,
             and url_glitch_video).
        video_encoder: Encoder descriptor dict from detect_video_encoder().
        source_h (int): Source video height for dimension calculation.
        custom_dims (tuple|None): Optional (width, height) override.

    Returns:
        str: Path to the generated .ts glitch transition file, or None on failure.
    """
    if custom_dims:
        out_w, out_h = custom_dims
    else:
        out_w, out_h = _get_render_dims(cfg, rasio, source_h=source_h)

    # Dimensions AND a recipe version in the filename. The cache is keyed by name
    # and returned unconditionally, so without the version a machine that already
    # had a glitch_ready_1080x1920.ts would keep serving the old one forever --
    # which is exactly how the black-frame recipe would have survived this fix.
    # Bump GLITCH_RECIPE_VERSION whenever the filter chain below changes.
    glitch_ts = f"glitch_ready_{out_w}x{out_h}_v{GLITCH_RECIPE_VERSION}.ts"
    if os.path.exists(glitch_ts):
        return glitch_ts

    # --- Primary: download glitch video from YouTube ---
    glitch_raw = "glitch_raw.mp4"
    use_downloaded = False
    if not os.path.exists(glitch_raw):
        try:
            url_glitch = getattr(cfg, "url_glitch_video", None)
            if url_glitch:
                print("⬇️ Downloading glitch video...", flush=True)
                YoutubeDL(
                    {
                        "format": "best[ext=mp4]",
                        "outtmpl": glitch_raw,
                        "quiet": True,
                        "extractor_args": {"youtube": ["player_client=android,web"]},
                    }
                ).download([url_glitch])
                use_downloaded = True
            else:
                print("⚠️ url_glitch_video not set, generating glitch locally...", flush=True)
        except Exception as e:
            print(f"⚠️ Glitch download failed: {e}. Falling back to local generation...", flush=True)
            # Clean up partial download
            if os.path.exists(glitch_raw):
                os.remove(glitch_raw)
    else:
        use_downloaded = True

    if use_downloaded and os.path.exists(glitch_raw):
        # Build filter from downloaded video
        algo = getattr(cfg, "video_scale_algo", "lanczos")
        if custom_dims:
            filter_g = f"scale={out_w}:{out_h}:flags={algo},setsar=1"
        else:
            if _is_vertical_ratio(rasio):
                w_part, h_part = RATIO_MAP.get(rasio, (9, 16))
                filter_g = (
                    f"crop=ih*{w_part}/{h_part}:ih:(iw-ih*{w_part}/{h_part})/2:0,"
                    f"scale={out_w}:{out_h}:flags={algo},setsar=1"
                )
            else:
                filter_g = f"scale={out_w}:{out_h}:flags={algo},setsar=1"

        cmd = (
            [
                "ffmpeg", "-y",
                "-ss", "0.2",
                "-t", "1",
                "-i", glitch_raw,
                "-vf", filter_g,
            ]
            + get_ts_encode_args(video_encoder, fps=30)
            + [glitch_ts]
        )
    else:
        # --- Fallback: generate VHS glitch noise via FFmpeg lavfi ---
        print("🎬 Generating glitch via FFmpeg lavfi...", flush=True)
        duration = 1.0
        # The base is mid-grey, NOT black. `noise` adds a signed offset, so on a
        # pure-black source every negative value clamps to 0 and only the
        # positive half survives: the result measured 19/255 mean luma -- a black
        # frame with faint speckle, which is what a user reported as a
        # "blackscreen bug" one second into every clip. blackdetect never fired,
        # because it is not quite black. On grey the noise spreads both ways and
        # the same recipe measures 126/255, which reads as static.
        cmd = (
            [
                "ffmpeg", "-y",
                "-f", "lavfi",
                "-i", f"color=c=gray:s={out_w}x{out_h}:d={duration}:r=30",
                "-f", "lavfi",
                "-i", "anullsrc=r=48000:cl=stereo",
                "-t", str(duration),
                "-vf", "noise=alls=100:allf=t+u,rgbashift=rh=20:bv=20",
            ]
            + get_ts_encode_args(video_encoder, fps=30)
            + [glitch_ts]
        )

    subprocess.run(
        cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    return glitch_ts


