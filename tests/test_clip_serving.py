"""How a clip is actually served: disposition, content type, byte ranges.

Three of the reported symptoms were one 401, but a fourth thing was wrong on its
own: `serve_output` passed `filename=` to Starlette's FileResponse, whose default
`content_disposition_type` is "attachment". A pasted clip URL therefore
downloaded a file instead of playing it, and a `poster` image was fetched with
the wrong disposition too. The player source and the Download button are the same
endpoint and were configured only for the second.

Range support is asserted rather than assumed. FileResponse has implemented it
since starlette 0.45 and the installed version is 1.3.1, so `<video>` seeking
needs no code -- but requirements.txt pins neither fastapi nor starlette and
there is no lockfile, so an old cached image layer could differ. A failure here
means seeking is broken, not that the test is wrong.
"""

import re

import pytest

from web.api import auth

TEST_TOKEN = "test-token-12345"
BEARER = {"Authorization": f"Bearer {TEST_TOKEN}"}
JOB = "abc123def456"
CLIP = "highlight_rank_1_ready.mp4"
BODY = bytes(range(256)) * 32  # 8192 bytes, and every byte value distinguishable


@pytest.fixture
def client(tmp_path, monkeypatch):
    """A TestClient whose OUTPUTS_DIR holds one real mp4 and one real jpg."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from web.api.routes import files as files_route

    job_dir = tmp_path / JOB
    job_dir.mkdir()
    (job_dir / CLIP).write_bytes(BODY)
    (job_dir / "thumbnail_rank_1.jpg").write_bytes(b"\xff\xd8\xff" + b"\x00" * 64)
    (job_dir / "highlight_rank_1.srt").write_text(
        "1\n00:00:00,000 --> 00:00:01,000\nhello\n", encoding="utf-8"
    )

    monkeypatch.setenv("API_TOKEN", TEST_TOKEN)
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    monkeypatch.setattr(auth, "_TOKEN", None)
    monkeypatch.setattr(files_route, "OUTPUTS_DIR", str(tmp_path))

    from web.api.app import app

    with TestClient(app) as test_client:
        yield test_client
    monkeypatch.setattr(auth, "_TOKEN", None)


def signed(filename=CLIP):
    return auth.media_url(JOB, filename, token=TEST_TOKEN)


# ----------------------------------------------------------------- disposition

def test_a_clip_is_inline_by_default(client):
    """What the <video> element needs. The default used to be attachment."""
    response = client.get(signed(), headers=BEARER)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("inline")


def test_download_1_makes_it_an_attachment(client):
    response = client.get(signed() + "&download=1", headers=BEARER)
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")


@pytest.mark.parametrize("suffix", ["", "&download=1"])
def test_the_filename_is_advertised_either_way(client, suffix):
    """The reported symptom was a saved file called something.json. The name has
    to survive in both dispositions, or the download saves under a bare id."""
    response = client.get(signed() + suffix, headers=BEARER)
    assert CLIP in response.headers["content-disposition"]


def test_download_is_not_part_of_the_signature(client):
    """A documented property, not an oversight: `download` selects one header and
    grants no access, so signing it would force two minted URLs per file."""
    url = signed()
    assert client.get(url).status_code == 200
    assert client.get(url + "&download=1").status_code == 200


# ---------------------------------------------------------------- content types

@pytest.mark.parametrize("filename,expected", [
    (CLIP, "video/mp4"),
    ("thumbnail_rank_1.jpg", "image/jpeg"),
    ("highlight_rank_1.srt", "text/plain"),
])
def test_each_output_kind_gets_its_real_media_type(client, filename, expected):
    """A clip served as application/octet-stream does not play, and the 401 that
    started all this was diagnosable only because its type was application/json."""
    response = client.get(signed(filename), headers=BEARER)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith(expected)


# ----------------------------------------------------------------- byte ranges

def test_ranges_are_advertised(client):
    response = client.get(signed(), headers=BEARER)
    assert response.headers.get("accept-ranges") == "bytes"


def test_a_range_request_answers_206_with_the_right_bytes(client):
    """Dragging a <video> scrubber is a Range request. Without 206 the browser
    re-downloads from zero on every seek, or refuses to seek at all."""
    response = client.get(signed(), headers={**BEARER, "Range": "bytes=0-1023"})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-1023/{len(BODY)}"
    assert response.content == BODY[:1024]


def test_a_mid_file_range_returns_that_slice(client):
    """A seek to the middle, which is the case that actually matters."""
    start, end = 4096, 4999
    response = client.get(
        signed(), headers={**BEARER, "Range": f"bytes={start}-{end}"}
    )
    assert response.status_code == 206
    assert response.content == BODY[start:end + 1]


def test_an_unsatisfiable_range_is_416(client):
    response = client.get(signed(), headers={**BEARER, "Range": "bytes=999999-"})
    assert response.status_code == 416


def test_a_range_works_on_a_signed_url_with_no_header(client):
    """The real shape of the request: the browser seeks without ever having sent
    a credential. If the signature were checked against the request path, or
    re-minted per response, this is where it would break."""
    response = client.get(signed(), headers={"Range": "bytes=100-199"})
    assert response.status_code == 206
    assert response.content == BODY[100:200]


# ------------------------------------------------------------------- not found

def test_a_missing_file_is_404_not_401(client):
    """A signature for a name that does not exist must not be confused with an
    authentication failure -- that distinction is what made the original bug hard
    to see."""
    response = client.get(signed("highlight_rank_99_ready.mp4"), headers=BEARER)
    assert response.status_code == 404


def test_the_listing_route_is_unchanged(client):
    """It is still header-only, and still reports what is on disk."""
    assert client.get(f"/api/outputs/{JOB}").status_code == 401
    body = client.get(f"/api/outputs/{JOB}", headers=BEARER).json()
    assert body["total"] == 3
    assert {f["filename"] for f in body["files"]} == {
        CLIP, "thumbnail_rank_1.jpg", "highlight_rank_1.srt"
    }


def test_the_traversal_guard_still_refuses_a_signed_escape(client, tmp_path):
    """Signature verification runs before resolve_output_path, so the path guard
    has to remain the thing that refuses."""
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("nope", encoding="utf-8")
    response = client.get(signed(f"../{outside.name}"), headers=BEARER)
    assert response.status_code in (400, 404)
    assert re.search(r"Invalid path|not found", response.text, re.I)


# ------------------------------------------------- thumbnails and .srt on a clip
#
# thumbnail_rank_N.jpg and highlight_rank_N.srt are written for every clip and
# were never exposed: ClipDetail.thumbnail_url was declared and never set, so the
# dashboard rendered a grid of black rectangles, and the .srt was reachable only
# inside the opaque metadata blob. Derived on READ so the jobs already in
# outputs/jobs.json -- which predate both fields -- work with no migration.

def persisted_clip(**overrides):
    """A clip record shaped like one written BEFORE these fields existed."""
    record = {
        "rank": 1,
        "filename": CLIP,
        "download_url": f"/api/outputs/{JOB}/{CLIP}",
        "metadata": {
            "rank": 1,
            "video_path": f"/app/outputs/{JOB}/{CLIP}",
            "thumbnail_path": f"/app/outputs/{JOB}/thumbnail_rank_1.jpg",
            "srt_path": f"/app/outputs/{JOB}/highlight_rank_1.srt",
        },
    }
    record.update(overrides)
    return record


def derive(record):
    # These five exercise the real ClipDetail, so they need pydantic, which CI
    # does not install (DEC-012). Skipped there, like every other test in this
    # file that touches the app.
    pytest.importorskip("pydantic")
    pytest.importorskip("fastapi")
    from web.api.models import ClipDetail
    from web.api.routes.jobs import _derive_missing_urls

    return _derive_missing_urls(ClipDetail(**record), JOB)


def test_an_old_clip_record_gains_both_urls():
    clip = derive(persisted_clip())
    assert clip.thumbnail_url == f"/api/outputs/{JOB}/thumbnail_rank_1.jpg"
    assert clip.srt_url == f"/api/outputs/{JOB}/highlight_rank_1.srt"


def test_the_container_path_in_the_manifest_does_not_leak():
    """metadata holds absolute CONTAINER paths (/app/outputs/...). Only the
    basename may reach a URL, or the link would 404 off the host."""
    clip = derive(persisted_clip())
    assert "/app/" not in clip.thumbnail_url
    assert "/app/" not in clip.srt_url


def test_a_clip_with_no_subtitle_gets_no_srt_url():
    """A clip with no speech legitimately has no .srt. It must be absent, not an
    empty string that renders a dead button."""
    record = persisted_clip()
    record["metadata"] = dict(record["metadata"], srt_path=None)
    clip = derive(record)
    assert clip.srt_url is None
    assert clip.thumbnail_url is not None


def test_urls_already_present_are_not_overwritten():
    """A record written by the current worker is already complete; deriving must
    not second-guess it."""
    record = persisted_clip(
        thumbnail_url="/api/outputs/other/t.jpg",
        srt_url="/api/outputs/other/s.srt",
    )
    clip = derive(record)
    assert clip.thumbnail_url == "/api/outputs/other/t.jpg"
    assert clip.srt_url == "/api/outputs/other/s.srt"


def test_a_record_with_no_metadata_at_all_does_not_explode():
    clip = derive(persisted_clip(metadata={}))
    assert clip.thumbnail_url is None and clip.srt_url is None


# ------------------------------------------------- signed URLs in a job response
#
# The fix, end to end: GET /api/jobs/{id} with a token returns URLs that a
# browser can then fetch with NO credential at all.

@pytest.fixture
def job_with_clips(client, monkeypatch):
    """A completed job in the store whose clip files exist under OUTPUTS_DIR."""
    from web.api import store

    record = {
        "id": JOB,
        "status": "completed",
        "clips": [persisted_clip()],
    }
    monkeypatch.setattr(store, "get_job", lambda job_id: dict(record) if job_id == JOB else None)
    return record


def clip_from_api(client):
    response = client.get(f"/api/jobs/{JOB}", headers=BEARER)
    assert response.status_code == 200
    return response.json()["clips"][0]


@pytest.mark.parametrize("field", ["download_url", "thumbnail_url", "srt_url"])
def test_every_media_url_in_a_job_response_is_signed(client, job_with_clips, field):
    url = clip_from_api(client)[field]
    assert "exp=" in url and "sig=" in url


@pytest.mark.parametrize("field", ["download_url", "thumbnail_url", "srt_url"])
def test_each_signed_url_is_fetchable_with_no_headers(client, job_with_clips, field):
    """This single assertion is the reported bug, inverted: a request shaped
    exactly like a <video src>, a poster load or a download click."""
    url = clip_from_api(client)[field]
    assert client.get(url).status_code == 200


def test_two_consecutive_reads_return_identical_urls(client, job_with_clips):
    """The bucketing property, asserted where it matters. If these differed, the
    dashboard's re-fetch would hand <video> a new src and restart playback."""
    assert clip_from_api(client) == clip_from_api(client)


def test_the_api_token_is_nowhere_in_the_response(client, job_with_clips):
    assert TEST_TOKEN not in client.get(f"/api/jobs/{JOB}", headers=BEARER).text


def test_the_stored_record_is_left_unsigned(client, job_with_clips):
    """Signing happens on the way out. A signed URL persisted into
    outputs/jobs.json would carry an expiry that outlives the record."""
    clip_from_api(client)
    stored = job_with_clips["clips"][0]
    assert "?" not in stored["download_url"]
    assert "exp=" not in stored["download_url"]


def test_a_job_list_signs_clips_too(client, job_with_clips, monkeypatch):
    """GET /api/jobs is the second route through _job_to_response. An unsigned
    URL here would work in the detail view and fail in the list."""
    from web.api import store

    monkeypatch.setattr(store, "list_jobs", lambda *a, **k: [dict(job_with_clips)])
    response = client.get("/api/jobs", headers=BEARER)
    assert response.status_code == 200
    for job in response.json()["jobs"]:
        for clip in job.get("clips", []):
            assert "sig=" in clip["download_url"]


# ------------------------------------------- what the dashboard actually renders
#
# Read from source, in the style of test_the_token_never_travels_in_a_query_string.
# These three are all silent failures: a missing poster just looks like a black
# box, and "?download=1" appended to a URL that already has a query string
# produces a 401 that looks like a signing bug.

import pathlib

JOB_DETAIL = (
    pathlib.Path(__file__).resolve().parents[1]
    / "web" / "dashboard" / "src" / "pages" / "JobDetail.jsx"
)


def job_detail_source():
    return JOB_DETAIL.read_text(encoding="utf-8")


def test_the_clip_card_uses_the_thumbnail_as_a_poster():
    """Otherwise the grid is black rectangles until each clip is played, which is
    what it looked like before thumbnail_url was populated at all."""
    assert "poster={clip.thumbnail_url" in job_detail_source()


def test_the_clip_card_offers_the_subtitle_file():
    assert "clip.srt_url" in job_detail_source()


def test_the_download_flag_is_appended_with_an_ampersand_not_a_question_mark():
    """The media URL already carries ?exp=&sig=. A second '?' makes `sig` part of
    the previous parameter's value, the signature never verifies, and the symptom
    is a 401 indistinguishable from a signing bug."""
    source = job_detail_source()
    assert "'?'}download=1" in source or "&download=1" in source
    # The naive form must not appear.
    assert "+ '?download=1'" not in source
    assert "}?download=1" not in source


def test_an_expired_signature_triggers_a_refresh_rather_than_a_silent_stall():
    """A tab left open past the TTL keeps playing from its buffer and then stalls
    on the next range request. Stalling silently is the symptom this whole change
    exists to remove."""
    source = job_detail_source()
    assert "onError=" in source
    assert "recoverExpiredMedia" in source


# ------------------------------------------- the SPA fallback that was not one
#
# Found while verifying this task in a real browser: opening /job/<id> directly
# answered {"detail":"Not Found"}. StaticFiles(html=True) does NOT fall back to
# index.html despite how it reads -- on a miss it looks for a 404.html and, not
# finding one, RAISES 404. So every client-side route (/new, /settings,
# /job/<id>) died on refresh, deep-link or a shared link. Clicking through from
# the home page worked, because that never leaves the SPA, which is why it
# survived: it is invisible unless you reload.

def spa_client(dist, monkeypatch):
    from fastapi.testclient import TestClient
    import web.api.app as appmod

    app = appmod.app
    saved = list(app.router.routes)
    # Reproduce the production branch: no "no dashboard built" fallback route,
    # and the real _SPAStaticFiles mounted last.
    app.router.routes = [
        r for r in saved
        if not (getattr(r, "path", None) == "/" and r.__class__.__name__ == "APIRoute")
    ]
    app.mount("/", appmod._SPAStaticFiles(directory=str(dist), html=True), name="ui-test")
    monkeypatch.setattr(app.router, "routes", app.router.routes, raising=False)

    client = TestClient(app)
    yield client
    app.router.routes = saved


@pytest.fixture
def spa(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "index-abc.js").write_text("console.log(1)", encoding="utf-8")
    yield from spa_client(dist, monkeypatch)


@pytest.mark.parametrize("path", [
    "/", "/clips", "/clips/new", "/clips/job/2773bd83c7b6", "/settings", "/story",
    "/story/anything",
    # The paths the product shipped with: the dashboard redirects them (DEC-094).
    "/new", "/job/2773bd83c7b6",
])
def test_every_client_side_route_serves_the_app(spa, path):
    response = spa.get(path)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "id=root" in response.text


def test_a_real_asset_is_still_served_as_itself(spa):
    response = spa.get("/assets/index-abc.js")
    assert response.status_code == 200
    assert "console.log" in response.text


@pytest.mark.parametrize("path", [
    "/assets/index-gone.js",
    "/favicon.ico",
    "/manifest.webmanifest",
    "/icon.svg",
])
def test_a_missing_file_is_still_404(spa, path):
    """Falling back here would answer a missing bundle or icon with HTML, turning
    a cache problem into a blank page with nothing in the console."""
    assert spa.get(path).status_code == 404


def test_the_api_is_untouched_by_the_fallback(spa):
    """The mount is last, so /api/* must still reach its routes rather than
    being answered with index.html."""
    assert spa.get("/api/health").status_code == 200
    assert spa.get("/api/jobs").status_code == 401
    assert spa.get("/api/nope").status_code == 404
