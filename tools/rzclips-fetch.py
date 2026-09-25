#!/usr/bin/env python3
"""Download a video on THIS machine and start a clipping job on your server.

Why this exists: sites score a download request by the IP's reputation before
anything else, and a datacenter range — which is what a VPS is — gets refused a
large share of the time regardless of cookies or proof-of-origin tokens. A home
connection does not. So the download happens where you are, and only the
finished files travel to the server.

    python rzclips-fetch.py --url "https://..." \
        --server https://your-machine.your-tailnet.ts.net \
        --token YOUR_API_TOKEN

The token is printed by the server on first start and stored in
``data/api_token``. You can also put it in the RZCLIPS_TOKEN environment
variable and leave --token off.

Standard library only, so it runs on a bare Python install with nothing but
yt-dlp on PATH. Tested against Python 3.10+.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
import uuid

SUBTITLE_EXTS = (".json3", ".vtt", ".srt")
VIDEO_EXTS = (".mp4", ".mkv", ".webm")

EXIT_OK = 0
EXIT_DOWNLOAD_FAILED = 2
EXIT_UPLOAD_FAILED = 3
EXIT_BAD_USAGE = 4


def log(message):
    print(message, flush=True)


# --------------------------------------------------------------- downloading

def yt_dlp_command(url, dest_dir, *, max_height, cookies_from_browser=None):
    """The yt-dlp argv. Separated so it can be inspected without running it."""
    template = os.path.join(dest_dir, "%(id)s.%(ext)s")
    args = [
        "yt-dlp",
        "--no-playlist",
        "--no-progress",
        "-f",
        f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
        f"b[height<={max_height}][ext=mp4]/"
        f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b",
        "--merge-output-format", "mp4",
        "--write-subs",
        "--write-auto-subs",
        # json3 first: it carries YouTube's own per-word timings, which is the
        # only real word-level timing short of running transcription.
        "--sub-format", "json3/vtt/srt/best",
        "--sub-langs", "all,-live_chat",
        "-o", template,
    ]
    if cookies_from_browser:
        args += ["--cookies-from-browser", cookies_from_browser]
    args.append(url)
    return args


def download(url, dest_dir, *, max_height=1080, cookies_from_browser=None):
    """Run yt-dlp. Returns ``(video_path, subtitle_path_or_None)``."""
    if shutil.which("yt-dlp") is None:
        raise SystemExit(
            "yt-dlp is not on PATH.\n"
            "  Install it with:  python -m pip install -U yt-dlp\n"
            "  It also needs a JavaScript runtime for YouTube: install Deno\n"
            "  (https://deno.com) or Node.js."
        )

    args = yt_dlp_command(
        url, dest_dir, max_height=max_height,
        cookies_from_browser=cookies_from_browser,
    )
    log(f"[1/4] Downloading with yt-dlp (up to {max_height}p)...")
    result = subprocess.run(args, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout or "").strip().splitlines()
        raise SystemExit(
            "yt-dlp failed:\n  " + "\n  ".join(tail[-6:] or ["(no output)"])
        )

    video = _largest(dest_dir, VIDEO_EXTS)
    if not video:
        raise SystemExit("yt-dlp reported success but produced no video file.")

    subtitle = _best_subtitle(dest_dir, video)
    return video, subtitle


def _largest(directory, extensions):
    found = [
        os.path.join(directory, name)
        for name in os.listdir(directory)
        if name.lower().endswith(tuple(extensions))
    ]
    return max(found, key=os.path.getsize) if found else None


def _best_subtitle(directory, video_path):
    """Pick by format: json3 has per-word timings, vtt and srt do not."""
    stem = os.path.splitext(os.path.basename(video_path))[0]
    candidates = []
    for name in os.listdir(directory):
        lowered = name.lower()
        if not lowered.startswith(stem.lower()):
            continue
        for index, ext in enumerate(SUBTITLE_EXTS):
            if lowered.endswith(ext):
                candidates.append((index, os.path.join(directory, name)))
                break
    if not candidates:
        return None
    candidates.sort()
    return candidates[0][1]


# ----------------------------------------------------------------- uploading

def post_multipart(url, token, file_path, *, opener=None, chunk_log=True):
    """Upload one file. Returns the parsed JSON body."""
    boundary = uuid.uuid4().hex
    filename = os.path.basename(file_path)
    content_type = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    size = os.path.getsize(file_path)

    head = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")

    with open(file_path, "rb") as handle:
        body = head + handle.read() + tail

    if chunk_log:
        log(f"      {filename} ({size / 1024 / 1024:.1f} MB)")

    request = urllib.request.Request(
        url,
        data=body,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        method="POST",
    )
    return _send(request, opener)


def post_json(url, token, payload, *, opener=None):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return _send(request, opener)


def _send(request, opener=None):
    send = opener or urllib.request.urlopen
    try:
        with send(request, timeout=3600) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 401:
            raise SystemExit(
                "The server rejected the API token (401).\n"
                "  Get it with: docker compose exec backend cat /app/data/api_token"
            )
        raise SystemExit(f"HTTP {exc.code} from {request.full_url}: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Could not reach {request.full_url}: {exc.reason}")
    return json.loads(raw) if raw.strip() else {}


# ---------------------------------------------------------------------- main

def build_payload(args, video_name, subtitle_name):
    payload = {
        "upload_filename": video_name,
        "clips": args.clips,
        "platform": args.platform,
        "source_url": args.url,
    }
    if subtitle_name:
        payload["transcript_filename"] = subtitle_name
    if args.language:
        payload["output_language"] = args.language
    return payload


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--url", required=True, help="The video URL to download.")
    parser.add_argument(
        "--server", required=True,
        help="Base URL of your server, e.g. https://vm.tailnet.ts.net",
    )
    parser.add_argument(
        "--token", default=os.environ.get("RZCLIPS_TOKEN", ""),
        help="API token (or set RZCLIPS_TOKEN).",
    )
    parser.add_argument("--clips", type=int, default=7, help="How many clips.")
    parser.add_argument(
        "--platform", default="auto",
        choices=["tiktok", "reels", "shorts", "auto", "long"],
        help="Target platform, which sets the clip duration window.",
    )
    parser.add_argument(
        "--language", default="",
        help="ISO-639-1 code for titles and captions. Default: follow the video.",
    )
    parser.add_argument("--max-height", type=int, default=1080)
    parser.add_argument(
        "--cookies-from-browser", default=None,
        help="Pass your browser's cookies to yt-dlp, e.g. 'firefox' or 'chrome'.",
    )
    parser.add_argument(
        "--keep", action="store_true",
        help="Keep the downloaded files instead of deleting them.",
    )
    args = parser.parse_args(argv)

    if not args.token:
        parser.error("no API token: pass --token or set RZCLIPS_TOKEN")

    server = args.server.rstrip("/")
    work_dir = tempfile.mkdtemp(prefix="rzclips_")

    try:
        video, subtitle = download(
            args.url, work_dir,
            max_height=args.max_height,
            cookies_from_browser=args.cookies_from_browser,
        )
        log(f"      video: {os.path.basename(video)}")
        log(f"      subs : {os.path.basename(subtitle) if subtitle else 'none'}")

        log("[2/4] Uploading the video...")
        video_name = post_multipart(
            f"{server}/api/upload", args.token, video
        ).get("filename") or os.path.basename(video)

        subtitle_name = None
        if subtitle:
            log("[3/4] Uploading the subtitles...")
            subtitle_name = post_multipart(
                f"{server}/api/upload", args.token, subtitle
            ).get("filename") or os.path.basename(subtitle)
        else:
            log("[3/4] No subtitles; the server will transcribe.")

        log("[4/4] Starting the job...")
        job = post_json(
            f"{server}/api/jobs", args.token,
            build_payload(args, video_name, subtitle_name),
        )
        job_id = job.get("id") or job.get("job_id") or ""
        log("")
        log(f"✅ Job started: {server}/clips/job/{job_id}")
        return EXIT_OK
    finally:
        if args.keep:
            log(f"Files kept in {work_dir}")
        else:
            shutil.rmtree(work_dir, ignore_errors=True)


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SystemExit as exc:
        if isinstance(exc.code, str):
            print(f"\n✖ {exc.code}", file=sys.stderr)
            raise SystemExit(EXIT_DOWNLOAD_FAILED)
        raise
