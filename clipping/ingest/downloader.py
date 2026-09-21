"""Best-effort video + subtitle download, with an honest failure mode.

This is deliberately not presented as reliable. YouTube scores a request by the
IP's reputation before it looks at anything else, and datacenter ranges — which
is what this VM is — are scored badly enough that a large share of first
contacts hit the bot wall regardless of cookies or proof-of-origin tokens. PO
tokens "may help"; they are not a bypass, and the project that provides them
says so.

So a failure here is a first-class outcome, not an exception: the job moves to
`needs_upload` and says what to do about it, which is to run the helper on a
machine with a residential IP (`tools/rzclips-fetch.py`) or to attach the file
by hand. That is the path that actually works, and pretending otherwise would
just produce mysterious failures.

Subtitle preference, best first:
  1. a manual (human-written) subtitle track — accurate, properly punctuated;
  2. auto-captions as **json3** — YouTube's own per-word timings, which is the
     only source of real word timestamps short of running transcription;
  3. auto-captions as **vtt** — usable since the rolling-cue fix, and the only
     option for many videos.

yt-dlp is imported lazily so the module stays importable without it.
"""

from __future__ import annotations

import glob
import os
import re
from collections import namedtuple

FetchResult = namedtuple(
    "FetchResult",
    "video_path subtitle_path title duration language error needs_upload",
)

# yt-dlp needs an external JavaScript runtime for YouTube's challenges. node is
# already in the image; deno is yt-dlp's own default and is documented as a
# compose profile for when node stops being enough.
DEFAULT_JS_RUNTIMES = ["node"]

SUBTITLE_PRIORITY = ("json3", "vtt", "srt")

# Messages that mean "this IP is not trusted", as opposed to a real error. A
# video that is private or deleted is a different thing and must not send the
# user off to run the helper for nothing.
_BOT_WALL_MARKERS = (
    "sign in to confirm",
    "confirm you're not a bot",
    "confirm you are not a bot",
    "not a bot",
    "failed to extract any player response",
    "unable to extract",
    "http error 429",
    "too many requests",
    "blocked it in your country",
    "this content isn't available",
    "please sign in",
    "cookies",
)

_FATAL_MARKERS = (
    "private video",
    "video unavailable",
    "removed by the uploader",
    "does not exist",
    "members-only",
    "age-restricted",
    "copyright",
)


class FetchError(RuntimeError):
    """The download failed for a reason the user cannot fix by uploading."""


def is_bot_wall(message):
    """Whether *message* says the IP was refused rather than the video missing.

    Checked in this order on purpose: a private or deleted video also fails to
    "extract a player response", and telling someone to go run a helper script
    for a video that no longer exists wastes their time.
    """
    text = str(message or "").lower()
    if any(marker in text for marker in _FATAL_MARKERS):
        return False
    return any(marker in text for marker in _BOT_WALL_MARKERS)


def build_options(dest_dir, *, cookies_path=None, pot_provider_url=None,
                  js_runtimes=None, max_height=1080, prefer_manual=True):
    """The yt-dlp options dict. Split out so tests can read it without network."""
    options = {
        "outtmpl": os.path.join(dest_dir, "%(id)s.%(ext)s"),
        "format": (
            f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/"
            f"b[height<={max_height}][ext=mp4]/"
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
        ),
        "merge_output_format": "mp4",
        "writesubtitles": prefer_manual,
        "writeautomaticsub": True,
        "subtitlesformat": "/".join(SUBTITLE_PRIORITY) + "/best",
        "subtitleslangs": ["all", "-live_chat"],
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "noplaylist": True,
        "retries": 3,
        "js_runtimes": list(js_runtimes or DEFAULT_JS_RUNTIMES),
    }

    if cookies_path and os.path.isfile(cookies_path):
        options["cookiefile"] = cookies_path

    if pot_provider_url:
        # bgutil's HTTP provider. Documented by its own authors as something
        # that "may help your traffic seem more legitimate" -- not a bypass.
        options["extractor_args"] = {
            "youtubepot-bgutilhttp": {"base_url": [pot_provider_url]}
        }

    return options


def fetch(url, dest_dir, *, cookies_path=None, pot_provider_url=None,
          js_runtimes=None, max_height=1080, prefer_manual=True,
          on_log=print, ydl_factory=None):
    """Download *url* into *dest_dir*.

    Returns a :class:`FetchResult`. On a bot wall it returns one with
    ``needs_upload=True`` and a human message rather than raising, because that
    outcome is expected often enough that the caller should handle it as a
    state, not an error.
    """
    os.makedirs(dest_dir, exist_ok=True)
    options = build_options(
        dest_dir,
        cookies_path=cookies_path,
        pot_provider_url=pot_provider_url,
        js_runtimes=js_runtimes,
        max_height=max_height,
        prefer_manual=prefer_manual,
    )

    factory = ydl_factory or _default_factory
    on_log(f"   ⬇️ Fetching {url}")

    try:
        with factory(options) as ydl:
            info = ydl.extract_info(url, download=True)
    except ImportError as exc:
        # yt-dlp is not installed. The user's remedy is exactly the remedy for a
        # refusal -- upload the file, or run the helper -- so this parks the job
        # rather than failing it with a Python error they cannot act on.
        on_log(f"   🚧 yt-dlp is not available on this server: {exc}")
        return FetchResult(
            None, None, None, None, None,
            "This server cannot download videos: yt-dlp is not installed.\n"
            + _remedies(url),
            True,
        )
    except Exception as exc:  # noqa: BLE001 - classified immediately
        message = str(exc)
        if is_bot_wall(message):
            on_log(f"   🚧 {url} was refused by the site: {message[:160]}")
            return FetchResult(
                None, None, None, None, None,
                _needs_upload_message(url), True,
            )
        on_log(f"   ✖ Download failed: {message[:200]}")
        raise FetchError(message) from exc

    if not isinstance(info, dict):
        raise FetchError("yt-dlp returned no metadata.")

    video_path = _resolve_video(info, dest_dir)
    if not video_path:
        return FetchResult(
            None, None, None, None, None,
            _needs_upload_message(url), True,
        )

    subtitle_path = pick_subtitle(dest_dir, os.path.basename(video_path))
    if subtitle_path:
        on_log(f"   📝 Subtitles: {os.path.basename(subtitle_path)}")
    else:
        on_log("   📝 No subtitles offered; the pipeline will transcribe.")

    return FetchResult(
        video_path,
        subtitle_path,
        info.get("title"),
        info.get("duration"),
        (info.get("language") or "").lower() or None,
        None,
        False,
    )


def pick_subtitle(dest_dir, video_filename):
    """The best subtitle file yt-dlp wrote beside *video_filename*.

    Preference is by FORMAT, not by language, because the pipeline transcribes
    whatever language it is given and json3 is the only format carrying real
    per-word timings. A manual track is preferred within a format: yt-dlp names
    auto-captions with an ``.orig`` or a language tag yt-dlp marks, and manual
    tracks sort first here by having no ``-orig`` marker.
    """
    stem = os.path.splitext(video_filename)[0]
    pattern = os.path.join(dest_dir, glob.escape(stem) + ".*")

    candidates = []
    for path in glob.glob(pattern):
        ext = os.path.splitext(path)[1].lstrip(".").lower()
        if ext not in SUBTITLE_PRIORITY:
            continue
        name = os.path.basename(path).lower()
        auto = "-orig" in name or ".orig." in name
        candidates.append((SUBTITLE_PRIORITY.index(ext), auto, path))

    if not candidates:
        return None
    candidates.sort()
    return candidates[0][2]


def _resolve_video(info, dest_dir):
    """The downloaded video file, from yt-dlp's own report where possible."""
    for key in ("requested_downloads",):
        for entry in info.get(key) or []:
            path = entry.get("filepath") or entry.get("_filename")
            if path and os.path.isfile(path):
                return path

    path = info.get("filepath") or info.get("_filename")
    if path and os.path.isfile(path):
        return path

    video_id = info.get("id")
    if video_id:
        for ext in ("mp4", "mkv", "webm"):
            candidate = os.path.join(dest_dir, f"{video_id}.{ext}")
            if os.path.isfile(candidate):
                return candidate
    return None


def _needs_upload_message(url):
    return (
        "This server could not download the video. Sites score a request by "
        "the IP's reputation first, and this machine is in a datacenter range, "
        "so downloads from here are refused often regardless of cookies.\n"
        + _remedies(url)
    )


def _remedies(url):
    return (
        "Two ways forward:\n"
        "  1. Run the helper on a machine with a home connection:\n"
        f"       python tools/rzclips-fetch.py --url \"{url}\" "
        "--server <your server> --token <your API token>\n"
        "  2. Download it yourself and attach the file to this job."
    )


def _default_factory(options):
    from yt_dlp import YoutubeDL

    return YoutubeDL(options)


def enabled(env=None):
    """Whether server-side fetching is switched on. Default: yes, best-effort."""
    env = os.environ if env is None else env
    return str(env.get("ENABLE_SERVER_FETCH", "1")).strip().lower() not in {
        "0", "false", "no",
    }
