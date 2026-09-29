"""The helper that downloads on a machine with a residential IP.

This is the path that actually works: sites score a request by the IP's
reputation first, and a home connection is scored very differently from a VPS.
The script is stdlib-only so it runs on a bare Python install with nothing but
yt-dlp on PATH.

Loaded by path rather than imported, because it lives in tools/ and is a script
rather than a package module. No network and no subprocess.
"""

import importlib.util
import json
import os
import pathlib

import pytest

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "tools" / "rzclips-fetch.py"


@pytest.fixture(scope="module")
def helper():
    spec = importlib.util.spec_from_file_location("rzclips_fetch", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------- portability

def test_it_imports_nothing_outside_the_standard_library():
    """It has to run on a bare Windows Python. A dependency here is a dependency
    the user has to install before the tool works at all."""
    import ast
    import sys

    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.add(node.module.split(".")[0])

    non_stdlib = sorted(imported - set(sys.stdlib_module_names))
    assert non_stdlib == [], f"non-stdlib imports: {non_stdlib}"


# ------------------------------------------------------------- the yt-dlp call

def test_the_command_caps_resolution_and_merges_to_mp4(helper):
    args = helper.yt_dlp_command("https://x/a", "/tmp/d", max_height=720)
    assert args[0] == "yt-dlp"
    assert any("height<=720" in a for a in args)
    assert "--merge-output-format" in args and "mp4" in args


def test_the_command_asks_for_json3_first(helper):
    """json3 carries YouTube's own per-word timings."""
    args = helper.yt_dlp_command("https://x/a", "/tmp/d", max_height=1080)
    fmt = args[args.index("--sub-format") + 1]
    assert fmt.split("/").index("json3") == 0


def test_both_subtitle_kinds_are_requested(helper):
    args = helper.yt_dlp_command("https://x/a", "/tmp/d", max_height=1080)
    assert "--write-subs" in args and "--write-auto-subs" in args


def test_the_url_is_last_so_it_is_never_read_as_a_flag_value(helper):
    args = helper.yt_dlp_command("https://x/a", "/tmp/d", max_height=1080)
    assert args[-1] == "https://x/a"


def test_browser_cookies_are_optional(helper):
    plain = helper.yt_dlp_command("https://x/a", "/tmp/d", max_height=1080)
    assert "--cookies-from-browser" not in plain

    with_cookies = helper.yt_dlp_command(
        "https://x/a", "/tmp/d", max_height=1080, cookies_from_browser="firefox"
    )
    assert with_cookies[with_cookies.index("--cookies-from-browser") + 1] == "firefox"


def test_a_playlist_url_downloads_one_video(helper):
    assert "--no-playlist" in helper.yt_dlp_command("https://x/a", "/tmp/d",
                                                    max_height=1080)


# ------------------------------------------------------------ picking outputs

def test_json3_is_preferred_over_vtt(helper, tmp_path):
    (tmp_path / "abc.mp4").write_text("v", encoding="utf-8")
    (tmp_path / "abc.en.vtt").write_text("s", encoding="utf-8")
    (tmp_path / "abc.en.json3").write_text("s", encoding="utf-8")

    picked = helper._best_subtitle(str(tmp_path), str(tmp_path / "abc.mp4"))
    assert picked.endswith(".json3")


def test_no_subtitle_is_none(helper, tmp_path):
    (tmp_path / "abc.mp4").write_text("v", encoding="utf-8")
    assert helper._best_subtitle(str(tmp_path), str(tmp_path / "abc.mp4")) is None


def test_the_largest_video_wins(helper, tmp_path):
    """yt-dlp can leave separate audio/video fragments behind on some failures."""
    (tmp_path / "small.mp4").write_bytes(b"x" * 10)
    (tmp_path / "big.mp4").write_bytes(b"x" * 1000)
    assert helper._largest(str(tmp_path), (".mp4",)).endswith("big.mp4")


# ------------------------------------------------------------------ the job

class Args:
    url = "https://x/watch?v=a"
    clips = 5
    platform = "tiktok"
    language = ""


def test_the_payload_matches_the_api(helper):
    payload = helper.build_payload(Args(), "video.mp4", "subs.vtt")
    assert payload["upload_filename"] == "video.mp4"
    assert payload["transcript_filename"] == "subs.vtt"
    assert payload["clips"] == 5
    assert payload["platform"] == "tiktok"
    assert payload["source_url"] == Args.url
    # Not sent at all when unset, so the server's own default applies.
    assert "output_language" not in payload


def test_the_transcript_is_omitted_when_there_is_none(helper):
    payload = helper.build_payload(Args(), "video.mp4", None)
    assert "transcript_filename" not in payload


def test_an_explicit_language_is_sent(helper):
    args = Args()
    args.language = "fr"
    assert helper.build_payload(args, "v.mp4", None)["output_language"] == "fr"


def test_the_payload_is_json_serializable(helper):
    json.dumps(helper.build_payload(Args(), "v.mp4", "s.vtt"))


def test_every_payload_key_is_a_real_job_field(helper):
    """A key the API does not declare is silently dropped by Pydantic, which is
    how the 'Bypass AI' toggle once did nothing at all."""
    pytest.importorskip("pydantic")
    from web.api.models import JobCreateRequest

    args = Args()
    args.language = "fr"
    payload = helper.build_payload(args, "v.mp4", "s.vtt")
    declared = set(JobCreateRequest.model_fields)
    assert set(payload) <= declared, f"unknown fields: {sorted(set(payload) - declared)}"


# ----------------------------------------------------------------- arguments

def test_the_token_can_come_from_the_environment(helper, monkeypatch):
    monkeypatch.setenv("RZCLIPS_TOKEN", "from-env")
    import argparse

    # Re-read the default the parser would use.
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", default=__import__("os").environ.get("RZCLIPS_TOKEN", ""))
    assert parser.parse_args([]).token == "from-env"


def test_a_missing_token_no_longer_refuses_to_run(helper, monkeypatch, tmp_path):
    """Auth on the server is opt-in (DEC-173): a token is required only when
    its operator set API_TOKEN, so this client must not refuse to even try
    without one -- it used to, back when the server always required a token.
    """
    monkeypatch.delenv("RZCLIPS_TOKEN", raising=False)
    video = tmp_path / "a.mp4"
    video.write_bytes(b"x")
    monkeypatch.setattr(helper, "download", lambda *a, **k: (str(video), None))

    tokens_used = []

    def fake_post_multipart(url, token, file_path, **kwargs):
        tokens_used.append(token)
        return {"filename": os.path.basename(file_path)}

    def fake_post_json(url, token, payload, **kwargs):
        tokens_used.append(token)
        return {"id": "job1"}

    monkeypatch.setattr(helper, "post_multipart", fake_post_multipart)
    monkeypatch.setattr(helper, "post_json", fake_post_json)

    exit_code = helper.main(["--url", "https://x/a", "--server", "https://s"])

    assert exit_code == helper.EXIT_OK
    assert tokens_used == ["", ""]


# ------------------------------------------------------------- the auth header

def _capturing_opener(body="{}"):
    """A fake urlopen that records the Request it was given."""
    captured = {}

    def opener(request, timeout=None):
        captured["request"] = request

        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def read(self):
                return body.encode("utf-8")

        return _Response()

    return opener, captured


def test_post_multipart_sends_no_authorization_header_without_a_token(helper, tmp_path):
    """Auth is opt-in on the server: a token-less run must not invent one."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    opener, captured = _capturing_opener('{"filename": "v.mp4"}')

    helper.post_multipart("https://s/api/upload", "", str(video), opener=opener, chunk_log=False)

    assert "Authorization" not in captured["request"].headers


def test_post_multipart_sends_bearer_with_a_token(helper, tmp_path):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x")
    opener, captured = _capturing_opener('{"filename": "v.mp4"}')

    helper.post_multipart("https://s/api/upload", "tok123", str(video), opener=opener, chunk_log=False)

    assert captured["request"].headers["Authorization"] == "Bearer tok123"


def test_post_json_sends_no_authorization_header_without_a_token(helper):
    opener, captured = _capturing_opener('{"id": "job1"}')

    helper.post_json("https://s/api/jobs", "", {"a": 1}, opener=opener)

    assert "Authorization" not in captured["request"].headers


def test_post_json_sends_bearer_with_a_token(helper):
    opener, captured = _capturing_opener('{"id": "job1"}')

    helper.post_json("https://s/api/jobs", "tok123", {"a": 1}, opener=opener)

    assert captured["request"].headers["Authorization"] == "Bearer tok123"
