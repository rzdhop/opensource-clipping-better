"""Signed story media: an AI Story episode's final video and its cover
(DEC-163, phase 4 stage 12).

A ``<video src>`` and an ``<a href download>`` are requests the BROWSER makes,
so they cannot carry the API token's header -- the problem DEC-048 solved for
clips. Story media gets its own signature rather than a bent clip rule: its own
HMAC key context, its own length-prefixed payload ``("episode-media",
story_id, ep, name, exp)``, a closed list of two file names, and a request that
must be exactly ``GET /api/stories/{story_id}/episodes/{ep}/media/{name}``.

A mistake here FAILS OPEN and is silent -- the header path keeps working, so
nothing else would notice -- which is why most of this file is about what a
signature must NOT open. The pure tests need only the standard library and run
in CI (DEC-012); the HTTP ones skip there, like tests/test_auth_token.py's.
"""

import hashlib
import hmac
import inspect
import re
import time
from types import SimpleNamespace

import pytest

from web.api import auth

TEST_TOKEN = "test-token-12345"
BEARER = {"Authorization": f"Bearer {TEST_TOKEN}"}
STORY = "0123456789ab"
OTHER_STORY = "ba9876543210"
VIDEO = "episode_final.mp4"
COVER = "cover.jpg"
JOB = "abc123def456"
CLIP = "highlight_rank_1_ready.mp4"
BODY = bytes(range(256)) * 32  # 8192 bytes, every byte value distinguishable
COVER_BODY = b"\xff\xd8\xff" + b"\x01" * 64
SECRET = "not yours"
NOW = 1_800_000_000
TTL = 12 * 3600


def media_path(story_id=STORY, ep=1, name=VIDEO):
    return f"/api/stories/{story_id}/episodes/{ep}/media/{name}"


def signed(story_id=STORY, ep=1, name=VIDEO, **kwargs):
    return auth.story_media_url(story_id, ep, name, token=TEST_TOKEN, **kwargs)


def query_of(url):
    return url.split("?", 1)[1]


# ============================================================ pure (stdlib only)

def test_the_allow_list_is_exactly_the_video_and_the_cover():
    """The only two files a story signature can ever open. The subtitles, the
    ledger view, the documents and the render folder stay header-only."""
    assert auth.STORY_MEDIA_NAMES == frozenset({VIDEO, COVER})


def test_the_clip_signature_constants_are_untouched():
    """RC-A5: DEC-048 is not bent to fit story media."""
    assert auth.MEDIA_KEY_CONTEXT == b"rzc-media-url-v1"
    assert auth.SIGNABLE_PREFIXES == ("/api/outputs/",)
    assert auth.STORY_MEDIA_KEY_CONTEXT == b"rzc-story-media-v1"


def test_the_story_key_is_neither_the_clip_key_nor_the_token():
    """Domain separation: a clip signature can never verify as a story one, and
    a captured story signature gives nothing to grind against the token."""
    story_key = auth._story_media_key(TEST_TOKEN)
    assert story_key != auth._media_key(TEST_TOKEN)
    assert story_key != TEST_TOKEN.encode("utf-8")
    assert len(story_key) == 32


def test_the_payload_is_length_prefixed():
    """Without the prefixes, ("a|b", "1") and ("a", "b|1") would sign the same
    string."""
    payload = auth._story_media_payload
    assert payload("a|b", "1", VIDEO, NOW) != payload("a", "b|1", VIDEO, NOW)
    assert payload(STORY, "1", "x|y", NOW) != payload(STORY, "1|x", "y", NOW)
    assert payload(STORY, "1", VIDEO, NOW).startswith(b"13:episode-media|")


def test_an_integer_episode_and_its_path_spelling_sign_the_same():
    """The episode page mints with ep=1 (an int); the route hands the validator
    ep="1" (its path parameter). They must be one signature."""
    exp = auth.media_expiry(now=NOW, ttl=TTL)
    assert auth.sign_story_media(STORY, 1, VIDEO, exp, token=TEST_TOKEN) == \
        auth.sign_story_media(STORY, "1", VIDEO, exp, token=TEST_TOKEN)


def test_a_story_url_has_exactly_the_route_shape():
    url = signed(now=NOW, ttl=TTL)
    assert re.fullmatch(
        r"/api/stories/0123456789ab/episodes/1/media/episode_final\.mp4\?exp=\d+&sig=[0-9a-f]{64}", url
    ), url
    assert TEST_TOKEN not in url


def test_the_url_is_byte_identical_inside_one_bucket():
    """The dashboard polls the episode every 4 s. A new src would restart the
    player and drop its cached bytes, so the URL must not move (media_expiry's
    bucketing, reused)."""
    first = signed(now=NOW, ttl=TTL)
    assert NOW % (TTL // 2) + 3600 < TTL // 2  # NOW + 1 h is still inside NOW's bucket
    assert signed(now=NOW + 4, ttl=TTL) == first
    assert signed(now=NOW + 3600, ttl=TTL) == first
    assert signed(now=NOW + TTL, ttl=TTL) != first


def test_a_story_signature_and_a_clip_signature_differ_for_the_same_names():
    exp = auth.media_expiry(now=NOW, ttl=TTL)
    assert auth.sign_story_media(STORY, 1, VIDEO, exp, token=TEST_TOKEN) != \
        auth.sign_media(STORY, VIDEO, exp, token=TEST_TOKEN)


def _sig_and_exp(url):
    return (re.search(r"[?&]sig=([^&]+)", url).group(1), re.search(r"[?&]exp=([^&]+)", url).group(1))


def test_a_signature_verifies_for_its_own_file_only():
    now = time.time()
    sig, exp = _sig_and_exp(signed())
    valid = auth.story_media_signature_is_valid
    assert valid(STORY, "1", VIDEO, exp, sig, token=TEST_TOKEN, now=now) is True
    assert valid(STORY, "2", VIDEO, exp, sig, token=TEST_TOKEN, now=now) is False
    assert valid(OTHER_STORY, "1", VIDEO, exp, sig, token=TEST_TOKEN, now=now) is False
    assert valid(STORY, "1", COVER, exp, sig, token=TEST_TOKEN, now=now) is False
    assert valid(STORY, "1", VIDEO, int(exp) + 1, sig, token=TEST_TOKEN, now=now) is False
    assert valid(STORY, "1", VIDEO, exp, sig, token="another-token", now=now) is False
    assert valid(STORY, "1", VIDEO, exp, sig, token=TEST_TOKEN, now=int(exp)) is False  # expired


@pytest.mark.parametrize("name", ["subtitles.ass", "cost_ledger.json", "render_manifest.json", "render",
                                  "../story.json", "", None])
def test_a_correct_hmac_over_a_name_outside_the_allow_list_is_refused(name):
    """The server's own key signed it, and it is still refused: the allow-list
    is checked, not assumed from the minting side."""
    exp = auth.media_expiry(ttl=TTL)
    sig = auth.sign_story_media(STORY, "1", name, exp, token=TEST_TOKEN)
    assert auth.story_media_signature_is_valid(STORY, "1", name, exp, sig, token=TEST_TOKEN) is False


@pytest.mark.parametrize("exp,sig", [
    ("abc", "zz"), ("", "zz"), (None, None), ("1e10", "00"), ("9" * 5000, "00"),
])
def test_garbage_is_a_refusal_and_never_raises(exp, sig):
    assert auth.story_media_signature_is_valid(STORY, "1", VIDEO, exp, sig, token=TEST_TOKEN) is False


def test_a_non_ascii_signature_is_a_refusal_not_a_type_error():
    """hmac.compare_digest raises TypeError on a non-ASCII str; the check must
    answer False instead."""
    _sig, exp = _sig_and_exp(signed())
    assert auth.story_media_signature_is_valid(STORY, "1", VIDEO, exp, "é" * 64, token=TEST_TOKEN) is False


# ---------------------------------------------- the request check, on a fake request

class _Query(dict):
    pass


def fake_request(method="GET", path=None, params=None, query=None):
    params = {"story_id": STORY, "ep": "1", "name": VIDEO} if params is None else params
    if path is None:
        path = media_path(params.get("story_id"), params.get("ep"), params.get("name"))
    if query is None:
        sig, exp = _sig_and_exp(auth.story_media_url(
            params.get("story_id") or STORY, params.get("ep") or "1", params.get("name") or VIDEO))
        query = {"exp": exp, "sig": sig}
    return SimpleNamespace(method=method, url=SimpleNamespace(path=path), path_params=params,
                           query_params=_Query(query))


@pytest.fixture
def pinned_token(monkeypatch):
    """current_token() answers TEST_TOKEN without touching data/api_token."""
    monkeypatch.setattr(auth, "_TOKEN", TEST_TOKEN)
    yield
    monkeypatch.setattr(auth, "_TOKEN", None)


def test_the_request_check_accepts_the_real_shape(pinned_token):
    assert auth.signed_story_media_request_is_valid(fake_request()) is True


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS", "get"])
def test_the_request_check_accepts_a_get_only(pinned_token, method):
    """The clip route answers HEAD with 405, so HEAD is refused here too."""
    assert auth.signed_story_media_request_is_valid(fake_request(method=method)) is False


@pytest.mark.parametrize("path", [
    f"/api/stories/{STORY}/episodes/1/voice/{VIDEO}",
    f"/api/stories/{STORY}/episodes/1/shots/{VIDEO}",
    f"/api/stories/{STORY}/episodes/1/media/{VIDEO}/",
    f"/api/stories/{STORY}/episodes/01/media/{VIDEO}",
    f"/api/outputs/{STORY}/{VIDEO}",
    f"/x/api/stories/{STORY}/episodes/1/media/{VIDEO}",
])
def test_the_request_check_needs_the_path_to_be_the_rebuilt_route(pinned_token, path):
    """Other story routes carry the same three parameters (the shots and voice
    blobs): the path equality is what keeps a signature off them."""
    assert auth.signed_story_media_request_is_valid(fake_request(path=path)) is False


@pytest.mark.parametrize("missing", ["story_id", "ep", "name"])
def test_the_request_check_needs_all_three_route_parameters(pinned_token, missing):
    params = {"story_id": STORY, "ep": "1", "name": VIDEO}
    request = fake_request(path=media_path(), params=params)
    del request.path_params[missing]
    assert auth.signed_story_media_request_is_valid(request) is False


def test_the_request_check_fails_closed_on_a_broken_request(pinned_token):
    class Exploding:
        method = "GET"

        @property
        def url(self):
            raise RuntimeError("boom")

    assert auth.signed_story_media_request_is_valid(Exploding()) is False
    assert auth.signed_story_media_request_is_valid(None) is False


def test_require_token_asks_the_story_check_only_after_the_header_and_the_clip_check():
    """The one change in require_token: a branch after the clip check."""
    source = inspect.getsource(auth.require_token)
    header = source.index("token_is_valid(presented")
    clip = source.index("signed_media_request_is_valid(request)")
    story = source.index("signed_story_media_request_is_valid(request)")
    assert header < clip < story


# ============================================ over HTTP, against the real app

@pytest.fixture
def media(tmp_path, monkeypatch):
    """The real app with a known token, and on disk: story STORY with ep01's
    video and cover (plus decoys: a shot, a line's take, the subtitles, the
    ledger view, the render folder), ep02's video only, ep03's video as a symlink
    to a file outside; story OTHER_STORY with ep01's video; a clip job."""
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    from clipping.aistory import store as story_store
    from clipping.aistory import workflow
    from web.api import store as job_store
    from web.api import worker
    from web.api.routes import files as files_route

    outputs = tmp_path / "outputs"
    outputs.mkdir()
    secret = tmp_path / "secret.txt"
    secret.write_text(SECRET, encoding="utf-8")

    for story_id in (STORY, OTHER_STORY):
        stories = story_store.StoryStore(str(outputs), id_factory=lambda sid=story_id: sid, on_log=lambda *_: None)
        stories.create(language="fr", now="2026-09-28T10:00:00+00:00")

    def ep_dir(story_id, ep):
        folder = outputs / "stories" / story_id / "episodes" / f"ep{ep:02d}"
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    ep1 = ep_dir(STORY, 1)
    (ep1 / VIDEO).write_bytes(BODY)
    (ep1 / COVER).write_bytes(COVER_BODY)
    (ep1 / "subtitles.ass").write_text("[Script Info]\n", encoding="utf-8")
    (ep1 / "cost_ledger.json").write_text("[]", encoding="utf-8")
    (ep1 / "render").mkdir()
    (ep1 / "assets" / "shots").mkdir(parents=True)
    (ep1 / "assets" / "shots" / "shot_01.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
    (ep1 / "assets" / "voice").mkdir(parents=True)
    (ep1 / "assets" / "voice" / "line_01.mp3").write_bytes(b"ID3" + b"\x00" * 32)
    (ep_dir(STORY, 2) / VIDEO).write_bytes(b"ep2" * 100)
    (ep_dir(STORY, 3) / VIDEO).symlink_to(secret)
    (ep_dir(OTHER_STORY, 1) / VIDEO).write_bytes(b"other" * 100)

    job = outputs / JOB
    job.mkdir()
    (job / CLIP).write_bytes(b"clip" * 100)

    monkeypatch.setattr(worker, "OUTPUTS_ROOT", str(outputs))
    monkeypatch.setattr(files_route, "OUTPUTS_DIR", str(outputs))
    monkeypatch.setattr(job_store, "_jobs", {})
    monkeypatch.setattr(job_store, "PERSIST_PATH", str(tmp_path / "jobs.json"))
    monkeypatch.setenv("WEB_SETTINGS_FILE", str(tmp_path / "settings.json"))
    monkeypatch.setenv("API_TOKEN", TEST_TOKEN)
    monkeypatch.delenv("DISABLE_AUTH", raising=False)
    monkeypatch.setattr(auth, "_TOKEN", None)

    # The episode page fills the media only beside a render; a fresh story has
    # none, so the page is given a stub render (the real one is exercised by
    # tests/test_stories_api_phase4.py's published episode).
    real_outputs = workflow.episode_outputs

    def with_a_render(stories, story, ep):
        view = real_outputs(stories, story, ep)
        view["render"] = view["render"] or {"state": "completed", "output": {"file": VIDEO, "sha256": "0" * 64}}
        return view

    monkeypatch.setattr(workflow, "episode_outputs", with_a_render)

    from web.api.app import app

    with TestClient(app) as client:
        yield SimpleNamespace(client=client, outputs=outputs, ep1=ep1, secret=secret, monkeypatch=monkeypatch)
    monkeypatch.setattr(auth, "_TOKEN", None)


# ------------------------------------------------------------ it plays

def test_the_signed_video_plays_with_no_header(media):
    response = media.client.get(signed())
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "video/mp4"
    assert response.content == BODY
    assert response.headers["content-disposition"].startswith("inline")
    assert response.headers["cache-control"] == "no-cache"
    assert response.headers.get("accept-ranges") == "bytes"
    assert response.headers.get("etag")


def test_a_range_on_the_signed_url_is_206_with_no_header(media):
    """Dragging the scrubber: the browser seeks without ever sending a
    credential."""
    response = media.client.get(signed(), headers={"Range": "bytes=0-99"})
    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes 0-99/{len(BODY)}"
    assert response.content == BODY[:100]


def test_the_signed_cover_is_a_jpeg(media):
    response = media.client.get(signed(name=COVER))
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == COVER_BODY


def test_download_1_makes_it_an_attachment_under_its_own_name(media):
    """download selects one header and grants nothing, so it is not signed
    (serve_output's rule); appended with '&' since the URL has a query."""
    response = media.client.get(signed() + "&download=1")
    assert response.status_code == 200
    assert response.headers["content-disposition"].startswith("attachment")
    assert VIDEO in response.headers["content-disposition"]
    assert media.client.get(signed()).headers["content-disposition"].startswith("inline")


def test_the_bearer_header_still_opens_the_route(media):
    response = media.client.get(media_path(), headers=BEARER)
    assert response.status_code == 200
    assert response.content == BODY


def test_the_bare_path_is_refused(media):
    response = media.client.get(media_path())
    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_a_missing_file_is_404_not_401(media):
    """ep02 has no cover yet: a valid signature for it is a 404, never confused
    with an authentication failure."""
    assert media.client.get(signed(ep=2, name=COVER)).status_code == 404


def test_a_symlinked_video_is_never_followed(media):
    for response in (media.client.get(signed(ep=3)), media.client.get(media_path(ep=3), headers=BEARER)):
        assert response.status_code == 404
        assert SECRET not in response.text


# -------------------------------------------------- it opens nothing else

OTHER_ROUTES = [
    ("GET", "/api/stories"),
    ("GET", f"/api/stories/{STORY}"),
    ("DELETE", f"/api/stories/{STORY}"),
    ("GET", f"/api/stories/{STORY}/episodes/1"),
    ("PATCH", f"/api/stories/{STORY}/episodes/1/assets"),
    ("POST", f"/api/stories/{STORY}/steps/render"),
    ("POST", f"/api/stories/{STORY}/approve/assets:1"),
    ("GET", f"/api/stories/{STORY}/estimate/render?ep=1"),
    ("GET", f"/api/stories/{STORY}/episodes/1/shots/shot_01.png"),
    ("GET", f"/api/stories/{STORY}/episodes/1/voice/line_01.mp3"),
    ("GET", f"/api/stories/{STORY}/files/preview_1.png"),
    ("GET", f"/api/stories/{STORY}/media/characters/char_a/portrait.png"),
    ("GET", f"/api/outputs/{JOB}/{CLIP}"),
    ("GET", f"/api/outputs/{JOB}"),
    ("GET", "/api/jobs"),
    ("GET", "/api/settings"),
    ("POST", "/api/upload"),
    ("POST", "/api/shutdown"),
]


@pytest.mark.parametrize("method,path", OTHER_ROUTES)
def test_a_story_signature_opens_nothing_else(media, method, path):
    """The query string reused verbatim on routes that have no business
    accepting it: the story's API, its other blobs, clip outputs, the rest."""
    joiner = "&" if "?" in path else "?"
    assert media.client.request(method, f"{path}{joiner}{query_of(signed())}").status_code == 401


@pytest.mark.parametrize("path", [
    f"/api/stories/{STORY}/episodes/1/shots/shot_01.png",
    f"/api/stories/{STORY}/episodes/1/voice/line_01.mp3",
    f"/api/outputs/{JOB}/{CLIP}",
])
def test_the_decoy_files_are_real(media, path):
    """So the 401s above are refusals of a file that is there."""
    assert media.client.get(path, headers=BEARER).status_code == 200


@pytest.mark.parametrize("path", [
    media_path(ep=2),                    # another episode of the story
    media_path(story_id=OTHER_STORY),    # another story's episode 1
    media_path(name=COVER),              # the other allow-listed name
])
def test_a_signature_for_one_file_does_not_open_another(media, path):
    assert media.client.get(f"{path}?{query_of(signed())}").status_code == 401
    assert media.client.get(path, headers=BEARER).status_code == 200


@pytest.mark.parametrize("name", ["render", "subtitles.ass", "render_manifest.json", "cost_ledger.json"])
def test_a_directory_or_a_file_outside_the_allow_list_stays_closed(media, name):
    """Even with a signature the server's key made over that very name; with the
    header it is a 404, never a listing or the file."""
    assert media.client.get(signed(name=name)).status_code == 401
    assert media.client.get(f"{media_path(name=name)}?{query_of(signed())}").status_code == 401
    assert media.client.get(media_path(name=name), headers=BEARER).status_code == 404


def test_the_media_folder_itself_is_no_route(media):
    for path in (f"/api/stories/{STORY}/episodes/1/media", f"/api/stories/{STORY}/episodes/1/media/"):
        response = media.client.get(f"{path}?{query_of(signed())}")
        assert response.status_code in (401, 404), path
        assert BODY[:16] not in response.content


@pytest.mark.parametrize("method", ["POST", "PUT", "DELETE", "PATCH", "HEAD"])
def test_another_method_on_the_signed_url_is_refused(media, method):
    """The route is GET only, so the router answers 405 -- or 404 when the
    dashboard's "/" mount is present (production, and this app after
    test_clip_serving's SPA tests), which takes an unmatched HEAD. Either way
    never a success and never the file; the method check itself is pinned on a
    fake request above."""
    response = media.client.request(method, signed())
    assert response.status_code in (401, 404, 405)
    assert response.content != BODY


def test_a_clip_signature_does_not_open_story_media(media):
    """Same (id, name) pair, the clip key: refused. And the story's payload
    signed with the clip key: refused too -- the keys are separate."""
    clip_query = query_of(auth.media_url(STORY, VIDEO, token=TEST_TOKEN))
    assert media.client.get(f"{media_path()}?{clip_query}").status_code == 401

    exp = auth.media_expiry()
    wrong_key = hmac.new(auth._media_key(TEST_TOKEN), auth._story_media_payload(STORY, "1", VIDEO, exp),
                             hashlib.sha256).hexdigest()
    assert media.client.get(f"{media_path()}?exp={exp}&sig={wrong_key}").status_code == 401


def test_a_story_signature_does_not_open_clip_media(media):
    story_query = query_of(auth.story_media_url(JOB, 1, CLIP, token=TEST_TOKEN))
    assert media.client.get(f"/api/outputs/{JOB}/{CLIP}?{story_query}").status_code == 401
    assert media.client.get(f"/api/outputs/{JOB}/{CLIP}?{query_of(signed())}").status_code == 401
    # and the clip's own signature still works (RC-A5)
    assert media.client.get(auth.media_url(JOB, CLIP, token=TEST_TOKEN)).status_code == 200


def test_an_expired_signature_is_refused(media):
    stale = signed(now=time.time() - 10 * 86400)
    assert media.client.get(stale).status_code == 401


def test_a_tampered_expiry_is_refused(media):
    url = signed()
    exp = int(re.search(r"[?&]exp=(\d+)", url).group(1))
    assert media.client.get(url.replace(f"exp={exp}", f"exp={exp + 86400}")).status_code == 401


@pytest.mark.parametrize("query", [
    "", "?exp=abc&sig=zz", "?sig=", "?exp=", "?exp=9999999999", "?sig=00", "?exp=9999999999&sig=%C3%A9",
])
def test_garbage_or_a_missing_part_is_a_401_not_a_500(media, query):
    assert media.client.get(f"{media_path()}{query}").status_code == 401


def test_a_signature_missing_its_expiry_is_refused(media):
    sig, _exp = _sig_and_exp(signed())
    assert media.client.get(f"{media_path()}?sig={sig}").status_code == 401


@pytest.mark.parametrize("path", [
    f"/api/stories/{STORY}/episodes/1/media/..%2f..%2fstory.json",
    f"/api/stories/{STORY}/episodes/1/media/..%2f..%2f..%2f..%2f..%2fsecret.txt",
    f"/api/stories/{STORY}/episodes/1/media/%2e%2e",
    f"/api/stories/..%2f{STORY}/episodes/1/media/{VIDEO}",
    f"/api/stories/{STORY}/episodes/..%2f..%2f/media/{VIDEO}",
    f"/api/stories/{STORY}/episodes/1/media/%2fetc%2fpasswd",
])
def test_a_traversal_is_refused_with_or_without_a_credential(media, path):
    for response in (media.client.get(f"{path}?{query_of(signed())}"), media.client.get(path, headers=BEARER)):
        assert response.status_code in (400, 401, 404), (path, response.status_code)
        assert SECRET not in response.text and "root:" not in response.text


def test_a_signature_minted_over_a_traversal_name_opens_nothing(media):
    """The server's key over "../../../../../secret.txt": the allow-list refuses
    it before any path is built."""
    name = "../../../../../secret.txt"
    exp = auth.media_expiry()
    sig = auth.sign_story_media(STORY, "1", name, exp, token=TEST_TOKEN)
    response = media.client.get(f"/api/stories/{STORY}/episodes/1/media/..%2f..%2f..%2f..%2f..%2fsecret.txt"
                                f"?exp={exp}&sig={sig}")
    assert response.status_code in (400, 401, 404)
    assert SECRET not in response.text


# ------------------------------------------------------- the episode page

def _page_media(media, ep=1):
    response = media.client.get(f"/api/stories/{STORY}/episodes/{ep}", headers=BEARER)
    assert response.status_code == 200, response.text
    return response.json()["render"]["media"]


def test_the_episode_page_mints_both_urls(media):
    urls = _page_media(media)
    assert urls == {"video_url": signed(), "cover_url": signed(name=COVER)}


def test_the_minted_urls_are_byte_identical_across_two_polls(media):
    """The 4 s poll: a moving src would restart playback."""
    assert _page_media(media) == _page_media(media)


def test_the_minted_urls_play_with_no_header(media):
    urls = _page_media(media)
    assert media.client.get(urls["video_url"]).content == BODY
    assert media.client.get(urls["cover_url"]).content == COVER_BODY
    ranged = media.client.get(urls["video_url"], headers={"Range": "bytes=100-199"})
    assert ranged.status_code == 206 and ranged.content == BODY[100:200]


def test_a_file_that_does_not_exist_gets_no_url(media):
    """ep02 has a video and no cover; ep03's video is a symlink (never
    served, so never offered); ep04 has no folder at all."""
    assert _page_media(media, 2) == {"video_url": signed(ep=2), "cover_url": None}
    assert _page_media(media, 3) == {"video_url": None, "cover_url": None}
    assert _page_media(media, 4) == {"video_url": None, "cover_url": None}


def test_the_urls_are_minted_on_the_way_out_and_never_stored(media):
    text = media.client.get(f"/api/stories/{STORY}/episodes/1", headers=BEARER).text
    assert TEST_TOKEN not in text
    for path in (media.outputs / "stories").rglob("*"):
        if path.is_file() and not path.is_symlink() and path.suffix in (".json", ".log"):
            assert "sig=" not in path.read_text(encoding="utf-8"), path


def test_the_route_is_under_the_routers_token():
    pytest.importorskip("fastapi")
    from web.api.routes import stories as stories_route

    route = next(r for r in stories_route.router.routes
                 if getattr(r, "path", None) == "/api/stories/{story_id}/episodes/{ep}/media/{name}")
    assert route.methods == {"GET"}
    assert any(dep.call is auth.require_token for dep in route.dependant.dependencies)
    assert set(stories_route._EPISODE_MEDIA_TYPES) == auth.STORY_MEDIA_NAMES
    assert {name for _field, name in stories_route._EPISODE_MEDIA_FIELDS} == auth.STORY_MEDIA_NAMES


# --------------------------------------------- the tokenless machine (DEC-092/105)

def test_a_tokenless_machine_still_opens_everything(media):
    """RC-P11: DISABLE_AUTH=1 answers every route without a credential, exactly
    as before -- a signature is neither needed nor checked."""
    media.monkeypatch.setenv("DISABLE_AUTH", "1")
    client = media.client
    assert client.get(media_path()).content == BODY
    assert client.get(f"{media_path()}?exp=abc&sig=zz").status_code == 200
    assert client.get(signed(now=time.time() - 10 * 86400)).status_code == 200
    assert client.get(media_path(), headers={"Range": "bytes=0-9"}).status_code == 206
    for path in ("/api/jobs", f"/api/stories/{STORY}/episodes/1/shots/shot_01.png", f"/api/outputs/{JOB}/{CLIP}"):
        assert client.get(path).status_code == 200, path
    urls = _page_media(media)
    assert client.get(urls["video_url"]).content == BODY
    assert client.get(media_path(name="subtitles.ass")).status_code == 404  # the allow-list is the route's too
