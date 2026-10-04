"""clipping/stock: the Pexels and Pixabay sources, the pool, search order, download
and credits (plan 23 stage B1). A fake opener stands in for the network; the
redirect tests use two local servers like test_stt_broll_redirects.py."""

import contextlib
import http.server
import io
import json
import logging
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path
from types import SimpleNamespace

import pytest

from clipping import stock
from clipping.stock import base, pexels, pixabay
from clipping.stock.base import StockClip

ROOT = Path(__file__).resolve().parents[1]
PEXELS_KEY, PIXABAY_KEY = "test-pexels-key", "test-pixabay-SECRET"


class FakeOpener:
    """Answers each ``open`` from ``responses`` (JSON-able, or an Exception to raise)."""

    def __init__(self, *responses):
        self.responses, self.requests = list(responses), []

    def open(self, request, timeout=None):
        self.requests.append(request)
        answer = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        if isinstance(answer, Exception):
            raise answer
        body = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
        return io.BytesIO(body)


def query_of(request):
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(request.full_url).query))


def pexels_video(vid, files, **extra):
    return {"id": vid, "duration": 12, "url": f"https://www.pexels.com/video/{vid}/",
            "user": {"name": "Ann", "url": "https://www.pexels.com/@ann"}, "video_files": files, **extra}


def pexels_file(link, quality="sd", width=640, height=1138, file_type="video/mp4"):
    return {"file_type": file_type, "quality": quality, "width": width, "height": height, "link": link}


def pixabay_hit(hid, large=(1080, 1920), medium=(540, 960), **extra):
    def item(size, name):
        return {"url": f"https://cdn.pixabay.com/{hid}-{name}.mp4", "width": size[0], "height": size[1]} if size else {}
    return {"id": hid, "pageURL": f"https://pixabay.com/videos/id-{hid}/", "tags": "city, night , lights",
            "duration": 15, "user": "bob", "user_id": 77,
            "videos": {"large": item(large, "large"), "medium": item(medium, "medium")}, **extra}


# ------------------------------------------------------------------- pexels

def test_pexels_request_orientation_and_quality_sort():
    videos = {"videos": [pexels_video(5, [
        pexels_file("https://cdn/sd.mp4", "sd", 720, 1280),
        pexels_file("https://cdn/hd-small.mp4", "hd", 1080, 1920),
        pexels_file("https://cdn/hd-big.mp4", "hd", 2160, 3840),
        pexels_file("https://cdn/clip.webm", "hd", 4000, 4000, file_type="video/webm")])]}
    fake = FakeOpener(videos)
    clips = pexels.SOURCE.candidates("city night", aspect="9:16", env={"PEXELS_API_KEY": PEXELS_KEY}, opener=fake)
    [request] = fake.requests
    assert query_of(request) == {"query": "city night", "orientation": "portrait", "per_page": "30",
                                 "size": "large", "resolution_name": "1080p"}
    assert request.get_header("Authorization") == PEXELS_KEY
    [clip] = clips
    assert (clip.key, clip.url, clip.width, clip.licence) == ("pexels:5", "https://cdn/hd-big.mp4", 2160, "Pexels License")
    assert (clip.author, clip.page_url, clip.duration_s) == ("Ann", "https://www.pexels.com/video/5/", 12.0)

    fake = FakeOpener({"videos": []})
    pexels.SOURCE.candidates("sea", aspect="16:9", env={"PEXELS_API_KEY": PEXELS_KEY}, opener=fake)
    assert query_of(fake.requests[0])["orientation"] == "landscape"
    for ratio in ("1:1", "3:4", "4:5"):
        assert stock.orientation_for(ratio) == "portrait"


def test_pexels_is_skipped_without_a_key_and_a_failure_names_no_key():
    assert not pexels.SOURCE.available({})
    assert pexels.SOURCE.candidates("x", aspect="9:16", env={}) == []
    fake = FakeOpener(OSError(f"boom {PEXELS_KEY}"))
    with pytest.raises(stock.StockError) as caught:
        pexels.SOURCE.candidates("x", aspect="9:16", env={"PEXELS_API_KEY": PEXELS_KEY}, opener=fake)
    assert PEXELS_KEY not in str(caught.value)


# ------------------------------------------------------------------ pixabay

def test_pixabay_request_orientation_filter_and_rendition(tmp_path):
    env = {"PIXABAY_API_KEY": PIXABAY_KEY, "STOCK_CACHE_DIR": str(tmp_path)}
    data = {"hits": [
        pixabay_hit(1),                                                   # portrait large wins over medium
        pixabay_hit(2, large=(1920, 1080), medium=(1280, 720)),           # landscape only: filtered out for 9:16
        pixabay_hit(3, large=(1920, 1080), medium=(540, 960)),            # only the medium matches
        pixabay_hit(4, large=None, medium=None)]}
    fake = FakeOpener(data)
    clips = pixabay.SOURCE.candidates("city", aspect="9:16", env=env, opener=fake, now=1000.0)
    assert [c.key for c in clips] == ["pixabay:1", "pixabay:3"]
    assert (clips[0].width, clips[0].url) == (1080, "https://cdn.pixabay.com/1-large.mp4")
    assert (clips[1].width, clips[1].height) == (540, 960)
    assert clips[0].tags == ("city", "night", "lights") and clips[0].licence == "Pixabay Content License"
    assert clips[0].author_url == "https://pixabay.com/users/bob-77/"
    params = query_of(fake.requests[0])
    assert params["q"] == "city" and params["safesearch"] == "true" and params["video_type"] == "film"
    assert params["key"] == PIXABAY_KEY and "per_page" in params
    assert urllib.parse.urlsplit(fake.requests[0].full_url).path == "/api/videos/"
    landscape = pixabay.SOURCE.candidates("city", aspect="16:9", env=env, opener=FakeOpener(data), now=1000.0)
    assert [c.key for c in landscape] == ["pixabay:2", "pixabay:3"]  # a different aspect, a different cache entry


def test_pixabay_answers_are_cached_for_24_hours_without_the_key(tmp_path):
    env = {"PIXABAY_API_KEY": PIXABAY_KEY, "STOCK_CACHE_DIR": str(tmp_path)}
    fake = FakeOpener({"hits": [pixabay_hit(1)]})
    pixabay.SOURCE.candidates("city", aspect="9:16", env=env, opener=fake, now=1000.0)
    pixabay.SOURCE.candidates("city", aspect="9:16", env=env, opener=fake, now=1000.0 + 23 * 3600)
    assert len(fake.requests) == 1
    pixabay.SOURCE.candidates("city", aspect="9:16", env=env, opener=fake, now=1000.0 + 24 * 3600 + 1)
    assert len(fake.requests) == 2
    [cached] = list((tmp_path / "pixabay").glob("*.json"))
    assert len(cached.stem) == 64
    assert PIXABAY_KEY not in cached.read_text()


def test_pixabay_key_never_reaches_a_log_line_or_an_exception_message(tmp_path, caplog):
    env = {"PIXABAY_API_KEY": PIXABAY_KEY, "STOCK_CACHE_DIR": str(tmp_path)}
    url = f"https://pixabay.com/api/videos/?key={PIXABAY_KEY}&q=city"
    fake = FakeOpener(OSError(f"HTTP 429 from {url}"))
    with pytest.raises(stock.StockError) as caught:
        pixabay.SOURCE.candidates("city", aspect="9:16", env=env, opener=fake, now=1.0)
    assert PIXABAY_KEY not in str(caught.value) and caught.value.__cause__ is None
    assert not list((tmp_path / "pixabay").glob("*.json"))  # a failure is never cached
    with caplog.at_level(logging.DEBUG, logger="clipping.stock"):
        assert stock.search("city", aspect="9:16", sources=[SimplePixabay(fake)], env=env) is None
    assert "failed" in caplog.text and PIXABAY_KEY not in caplog.text
    assert stock.redact(url) == "https://pixabay.com/api/videos/?key=REDACTED&q=city"


class SimplePixabay:
    """The real Pixabay source with a fake opener, for the search() path."""
    name = "pixabay"
    shuffle = True

    def __init__(self, opener):
        self.opener = opener

    def available(self, env):
        return pixabay.SOURCE.available(env)

    def candidates(self, query, **kwargs):
        return pixabay.SOURCE.candidates(query, opener=self.opener, now=1.0, **kwargs)


# --------------------------------------------------------- pool and search

def clip(provider, ident, **kwargs):
    return StockClip(provider=provider, id=str(ident), url=f"https://cdn/{provider}{ident}.mp4", width=1080,
                     height=1920, duration_s=kwargs.pop("duration_s", 10.0), **kwargs)


class FakeSource:
    def __init__(self, name, clips=(), available=True, fail=False, shuffle=False):
        self.name, self.clips, self._available, self.fail, self.shuffle, self.calls = name, list(clips), available, fail, shuffle, []

    def available(self, env):
        return self._available

    def candidates(self, query, **kwargs):
        self.calls.append((query, kwargs["aspect"], kwargs["min_duration_s"]))
        if self.fail:
            raise stock.StockError("down")
        return list(self.clips)


def test_pool_dedupes_then_resets_when_the_query_runs_out():
    pool = stock.StockPool()
    a, b = clip("pexels", 1), clip("pixabay", 9)
    assert [a.key, b.key] == ["pexels:1", "pixabay:9"]
    assert pool.take([a, b]) == a and a.key in pool
    assert pool.take([a, b]) == b
    assert pool.take([a, b]) == a  # exhausted: reset, handed out again
    assert pool.take([]) is None
    assert len(pool) == 1  # only the reset's pick is marked; the other is fresh again
    assert pool.take([a, b]) == b


def test_search_tries_the_sources_in_order_and_returns_the_first_match():
    local, px, pb = FakeSource("local", [], ), FakeSource("pexels", [clip("pexels", 1)]), FakeSource("pixabay", [clip("pixabay", 2)])
    pool = stock.StockPool()
    got = stock.search("city  night", aspect="16:9", min_duration_s=4, sources=[local, px, pb], pool=pool)
    assert got.key == "pexels:1" and pb.calls == []
    assert local.calls == [("city night", "16:9", 4)]
    assert stock.search("city", aspect="16:9", sources=[local, px, pb], pool=pool).key == "pexels:1"  # reset, same clip
    assert stock.search("", aspect="16:9", sources=[px], pool=pool) is None


def test_search_skips_unavailable_and_failing_sources_and_short_clips():
    down, keyless = FakeSource("pixabay", fail=True), FakeSource("pexels", [clip("pexels", 1)], available=False)
    short = FakeSource("local", [clip("local", "a", duration_s=2.0)])
    ok = FakeSource("pexels", [clip("pexels", 3)])
    assert stock.search("x", aspect="9:16", min_duration_s=5, sources=[down, keyless, short, ok], pool=stock.StockPool()).key == "pexels:3"
    assert keyless.calls == []
    assert stock.search("x", aspect="9:16", sources=[down], pool=stock.StockPool()) is None


def test_search_by_name_skips_keyless_installs(tmp_path):
    assert stock.search("x", aspect="9:16", sources="local,pexels,pixabay,nope", env={}, pool=stock.StockPool()) is None
    assert base.parse_sources(None) == ("local", "pexels", "pixabay")
    assert base.parse_sources(" Pexels , local ") == ("pexels", "local")


# ---------------------------------------------------------------- download

def test_download_writes_a_part_file_then_renames(tmp_path):
    dest = tmp_path / "out" / "broll.mp4"
    fake = FakeOpener(b"mp4 bytes")
    assert stock.download(clip("pexels", 1), dest, opener_=fake) == str(dest)
    assert dest.read_bytes() == b"mp4 bytes" and not Path(str(dest) + ".part").exists()
    assert fake.requests[0].full_url == "https://cdn/pexels1.mp4"

    class Boom(FakeOpener):
        def open(self, request, timeout=None):
            class Half(io.BytesIO):
                def read(self, *_):
                    raise OSError("cut")
            return Half()

    bad = tmp_path / "bad.mp4"
    with pytest.raises(stock.StockError):
        stock.download(clip("pexels", 2), bad, opener_=Boom())
    assert not bad.exists() and not Path(str(bad) + ".part").exists()
    with pytest.raises(stock.StockError):
        stock.download(StockClip("pexels", "3", "file:///etc/passwd", 1, 1, 1.0), bad)


def test_download_copies_a_local_clip_and_refuses_a_symlink(tmp_path):
    src = tmp_path / "src.mp4"
    src.write_bytes(b"local bytes")
    local = StockClip("local", "abc", str(src), 1080, 1920, 5.0)
    assert Path(stock.download(local, tmp_path / "copy.mp4")).read_bytes() == b"local bytes"
    link = tmp_path / "link.mp4"
    link.symlink_to(src)
    with pytest.raises(stock.StockError):
        stock.download(StockClip("local", "abc", str(link), 1, 1, 1.0), tmp_path / "x.mp4")


# ----------------------------------------------------------------- credits

def test_credit_lines_and_records():
    px = clip("pexels", 1, author="Ann", page_url="https://www.pexels.com/video/1/", licence="Pexels License",
              licence_url="https://www.pexels.com/license/")
    assert stock.credit_line(px) == "Video by Ann on Pexels (https://www.pexels.com/video/1/), Pexels License"
    record = stock.credit_record(px)
    json.dumps(record)
    assert record["licence_url"] == "https://www.pexels.com/license/" and "warning" not in record
    pb = clip("pixabay", 9, author="bob", licence="Pixabay Content License", licence_url="https://pixabay.com/service/license-summary/")
    assert "Pixabay Content License" in stock.credit_line(pb) and "on Pixabay" in stock.credit_line(pb)
    mine = stock.credit_record(clip("local", "x", licence="CC-BY 4.0", author="Me"))
    assert mine["licence"] == "CC-BY 4.0" and "warning" not in mine
    unknown = stock.credit_record(clip("local", "y", licence=""))
    assert unknown["licence"] == "unknown" and "warning" in unknown


# ------------------------------------------------ redirects, no cross-origin key

@contextlib.contextmanager
def _server():
    """A server on 127.0.0.1 recording (method, path, query, lower-cased headers)."""
    seen, routes = [], {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            path, _, query = self.path.partition("?")
            seen.append((self.command, path, query, {n.lower(): v for n, v in self.headers.items()}))
            status, headers, body = routes.get(path, (404, {}, b""))
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{server.server_address[1]}", seen=seen, routes=routes)
    finally:
        server.shutdown()
        server.server_close()


def test_the_pexels_search_does_not_carry_the_key_to_another_origin(monkeypatch):
    with _server() as b, _server() as a:
        monkeypatch.setattr(pexels, "PEXELS_VIDEO_SEARCH_URL", f"{a.url}/videos/search")
        a.routes["/videos/search"] = (302, {"Location": f"{b.url}/moved"}, b"")
        video = pexels_video(7, [pexels_file(f"{b.url}/clip.mp4", "hd", 1080, 1920)])
        b.routes["/moved"] = (200, {"Content-Type": "application/json"}, json.dumps({"videos": [video]}).encode())
        b.routes["/clip.mp4"] = (200, {"Content-Type": "video/mp4"}, b"mp4 bytes")
        found = stock.search("city", aspect="9:16", sources=["pexels"], env={"PEXELS_API_KEY": PEXELS_KEY}, pool=stock.StockPool())
        assert found.key == "pexels:7"
    [(_, _, _, first)] = a.seen
    assert first["authorization"] == PEXELS_KEY  # the host it was addressed to got it
    assert [(m, p) for m, p, _, _ in b.seen] == [("GET", "/moved")]
    assert not [h for _, _, _, h in b.seen if "authorization" in h]


def test_the_pixabay_search_does_not_forward_the_key_to_another_origin(monkeypatch, tmp_path):
    env = {"PIXABAY_API_KEY": PIXABAY_KEY, "STOCK_CACHE_DIR": str(tmp_path)}
    with _server() as b, _server() as a:
        monkeypatch.setattr(pixabay, "PIXABAY_VIDEO_SEARCH_URL", f"{a.url}/api/videos/")
        a.routes["/api/videos/"] = (302, {"Location": f"{b.url}/moved"}, b"")
        b.routes["/moved"] = (200, {"Content-Type": "application/json"}, json.dumps({"hits": [pixabay_hit(4)]}).encode())
        found = stock.search("city", aspect="9:16", sources=["pixabay"], env=env, pool=stock.StockPool())
        assert found.key == "pixabay:4"
    assert PIXABAY_KEY in a.seen[0][2]
    assert [(m, p) for m, p, _, _ in b.seen] == [("GET", "/moved")]
    assert all(PIXABAY_KEY not in query and "authorization" not in h and "x-api-key" not in h for _, _, query, h in b.seen)


def test_the_clip_download_does_not_carry_a_credential_to_another_origin(tmp_path):
    with _server() as b, _server() as a:
        a.routes["/c.mp4"] = (302, {"Location": f"{b.url}/real.mp4"}, b"")
        b.routes["/real.mp4"] = (200, {"Content-Type": "video/mp4"}, b"data")
        item = StockClip("pexels", "1", f"{a.url}/c.mp4", 1, 1, 1.0)
        stock.download(item, tmp_path / "c.mp4")
    assert (tmp_path / "c.mp4").read_bytes() == b"data"


# --------------------------------------------------------------- stdlib only

def test_the_package_imports_with_only_the_stdlib():
    code = ("import sys, clipping.stock\n"
            "from clipping.stock import base, pexels, pixabay, local, credits\n"
            "bad = [m for m in ('cv2', 'mediapipe', 'requests', 'numpy', 'PIL', 'yt_dlp', 'clipping.studio') if m in sys.modules]\n"
            "assert not bad, bad\n")
    result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
