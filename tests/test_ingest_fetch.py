"""Server-side downloading, and the honest failure it usually produces.

Sites score a request by the IP's reputation before anything else, and this
machine is in a datacenter range. A refusal is therefore an expected outcome,
not an exception — the job parks in `needs_upload` and says what to do. These
tests pin that distinction, because getting it wrong either fails jobs that
could be rescued or sends the user chasing a video that does not exist.

No network: YoutubeDL is injected.
"""

import os

import pytest

from clipping.ingest import downloader as fetch_mod


# ------------------------------------------------------- classifying failures

@pytest.mark.parametrize("message", [
    "ERROR: Sign in to confirm you're not a bot",
    "Sign in to confirm you are not a bot. Use --cookies-from-browser",
    "Failed to extract any player response",
    "HTTP Error 429: Too Many Requests",
    "Unable to extract yt initial data",
    "Please sign in",
])
def test_a_refused_request_is_recognised(message):
    assert fetch_mod.is_bot_wall(message) is True


@pytest.mark.parametrize("message", [
    "Private video. Sign in if you've been granted access to this video",
    "Video unavailable",
    "This video has been removed by the uploader",
    "Join this channel to get access to members-only content",
    "Sign in to confirm your age. This video may be age-restricted",
])
def test_a_missing_or_restricted_video_is_not(message):
    """A private video also fails to 'extract a player response'. Telling
    someone to go run a helper script for a video that no longer exists wastes
    their time, so the fatal markers are checked first."""
    assert fetch_mod.is_bot_wall(message) is False


def test_an_empty_message_is_not_a_bot_wall():
    assert fetch_mod.is_bot_wall("") is False
    assert fetch_mod.is_bot_wall(None) is False


# ------------------------------------------------------------------- options

def test_the_format_caps_the_height():
    options = fetch_mod.build_options("/tmp/x", max_height=720)
    assert "height<=720" in options["format"]
    assert options["merge_output_format"] == "mp4"


def test_json3_is_preferred_over_vtt():
    """json3 carries YouTube's own per-word timings, which is the only real
    word-level timing short of running transcription."""
    options = fetch_mod.build_options("/tmp/x")
    formats = options["subtitlesformat"].split("/")
    assert formats.index("json3") < formats.index("vtt")


def test_both_manual_and_automatic_subtitles_are_requested():
    options = fetch_mod.build_options("/tmp/x")
    assert options["writesubtitles"] is True
    assert options["writeautomaticsub"] is True


def test_a_javascript_runtime_is_configured():
    """yt-dlp needs one for YouTube's challenges; node is in the image."""
    assert fetch_mod.build_options("/tmp/x")["js_runtimes"] == ["node"]


def test_a_playlist_url_downloads_one_video():
    assert fetch_mod.build_options("/tmp/x")["noplaylist"] is True


def test_cookies_are_only_passed_when_the_file_exists(tmp_path):
    missing = str(tmp_path / "nope.txt")
    assert "cookiefile" not in fetch_mod.build_options("/tmp/x", cookies_path=missing)

    real = tmp_path / "cookies.txt"
    real.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    options = fetch_mod.build_options("/tmp/x", cookies_path=str(real))
    assert options["cookiefile"] == str(real)


def test_the_pot_provider_is_only_configured_when_asked():
    assert "extractor_args" not in fetch_mod.build_options("/tmp/x")
    options = fetch_mod.build_options("/tmp/x", pot_provider_url="http://127.0.0.1:4416")
    assert options["extractor_args"]["youtubepot-bgutilhttp"]["base_url"] == [
        "http://127.0.0.1:4416"
    ]


# -------------------------------------------------------------- subtitle pick

def _touch(directory, name):
    path = os.path.join(directory, name)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("x")
    return path


def test_json3_wins_when_several_formats_were_written(tmp_path):
    for name in ("abc.en.vtt", "abc.en.json3", "abc.en.srt"):
        _touch(str(tmp_path), name)
    picked = fetch_mod.pick_subtitle(str(tmp_path), "abc.mp4")
    assert picked.endswith(".json3")


def test_vtt_is_used_when_json3_was_not_offered(tmp_path):
    _touch(str(tmp_path), "abc.fr.vtt")
    assert fetch_mod.pick_subtitle(str(tmp_path), "abc.mp4").endswith(".vtt")


def test_a_manual_track_beats_an_auto_one_of_the_same_format(tmp_path):
    _touch(str(tmp_path), "abc.en-orig.vtt")
    _touch(str(tmp_path), "abc.en.vtt")
    picked = os.path.basename(fetch_mod.pick_subtitle(str(tmp_path), "abc.mp4"))
    assert picked == "abc.en.vtt"


def test_no_subtitles_is_none_not_an_error(tmp_path):
    assert fetch_mod.pick_subtitle(str(tmp_path), "abc.mp4") is None


def test_another_videos_subtitles_are_not_picked_up(tmp_path):
    _touch(str(tmp_path), "other.en.vtt")
    assert fetch_mod.pick_subtitle(str(tmp_path), "abc.mp4") is None


# ------------------------------------------------------------------ fetching

class FakeYDL:
    def __init__(self, result=None, error=None):
        self.result = result
        self.error = error
        self.calls = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def extract_info(self, url, download=True):
        self.calls.append((url, download))
        if self.error:
            raise self.error
        return self.result


def factory_for(ydl):
    return lambda options: ydl


def test_a_successful_fetch_returns_the_files(tmp_path):
    video = _touch(str(tmp_path), "vid123.mp4")
    _touch(str(tmp_path), "vid123.en.json3")
    ydl = FakeYDL({"id": "vid123", "title": "A video", "duration": 120,
                   "language": "FR", "filepath": video})

    result = fetch_mod.fetch("https://x/watch?v=vid123", str(tmp_path),
                             on_log=lambda *a: None, ydl_factory=factory_for(ydl))

    assert result.video_path == video
    assert result.subtitle_path.endswith(".json3")
    assert result.title == "A video"
    assert result.language == "fr"
    assert result.needs_upload is False
    assert result.error is None


def test_a_refusal_parks_the_job_instead_of_raising(tmp_path):
    ydl = FakeYDL(error=RuntimeError("Sign in to confirm you're not a bot"))

    result = fetch_mod.fetch("https://x/watch?v=a", str(tmp_path),
                             on_log=lambda *a: None, ydl_factory=factory_for(ydl))

    assert result.needs_upload is True
    assert result.video_path is None
    assert "rzclips-fetch.py" in result.error
    assert "datacenter" in result.error


def test_a_real_error_raises(tmp_path):
    """A deleted video is not something an upload can fix in the same way, and
    must not be dressed up as one."""
    ydl = FakeYDL(error=RuntimeError("Video unavailable"))
    with pytest.raises(fetch_mod.FetchError):
        fetch_mod.fetch("https://x/watch?v=a", str(tmp_path),
                        on_log=lambda *a: None, ydl_factory=factory_for(ydl))


def test_a_missing_output_file_parks_the_job(tmp_path):
    """yt-dlp reported success but nothing is on disk."""
    ydl = FakeYDL({"id": "nothing", "title": "t"})
    result = fetch_mod.fetch("https://x/a", str(tmp_path),
                             on_log=lambda *a: None, ydl_factory=factory_for(ydl))
    assert result.needs_upload is True


def test_the_video_is_found_by_id_when_ytdlp_reports_no_path(tmp_path):
    _touch(str(tmp_path), "vid9.mp4")
    ydl = FakeYDL({"id": "vid9"})
    result = fetch_mod.fetch("https://x/a", str(tmp_path),
                             on_log=lambda *a: None, ydl_factory=factory_for(ydl))
    assert result.video_path.endswith("vid9.mp4")


def test_a_video_with_no_subtitles_still_succeeds(tmp_path):
    video = _touch(str(tmp_path), "v.mp4")
    ydl = FakeYDL({"id": "v", "filepath": video})
    result = fetch_mod.fetch("https://x/a", str(tmp_path),
                             on_log=lambda *a: None, ydl_factory=factory_for(ydl))
    assert result.video_path == video
    assert result.subtitle_path is None
    assert result.needs_upload is False


# ------------------------------------------------------------------ the flag

def test_server_fetch_is_on_by_default():
    assert fetch_mod.enabled({}) is True


@pytest.mark.parametrize("value", ["0", "false", "no", "FALSE"])
def test_it_can_be_turned_off(value):
    assert fetch_mod.enabled({"ENABLE_SERVER_FETCH": value}) is False


def test_a_missing_ytdlp_parks_the_job_rather_than_failing_it(tmp_path):
    """The remedy is identical to a refusal -- upload it, or run the helper --
    so a Python ImportError must not reach the user as a failed job."""
    def factory(options):
        raise ImportError("No module named 'yt_dlp'")

    result = fetch_mod.fetch("https://x/a", str(tmp_path),
                             on_log=lambda *a: None, ydl_factory=factory)

    assert result.needs_upload is True
    assert "yt-dlp is not installed" in result.error
    assert "rzclips-fetch.py" in result.error
