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
