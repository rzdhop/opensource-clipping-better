"""Portrait thumbnails (dashboard overhaul stage 5, DEC-257).

A speaker avatar, a stories-list cover and an entity tile used to fetch the
full portrait -- 550-640 kB -- to draw 28 to 340 px of it. ``GET
/api/stories/{id}/media/{kind}/{eid}/{name}?size=thumb`` answers a 480 px-wide
JPEG instead, made on the first request and kept next to the original as
``<name>.thumb.jpg`` (``clipping/aistory/thumbs.py``); a later request serves
the kept file until the original changes. Everything else about the route is
the original's: the same name rules, the same 404s, never a symlink followed --
neither the original's nor one planted where the thumbnail goes.

The rule-level tests at the top need only the standard library and run in CI
(DEC-012). Making a thumbnail needs Pillow (a declared dependency of the
image, not installed in CI), and the route tests need fastapi: those skip
where either is missing, like tests/test_stories_api_list.py's.
"""

from __future__ import annotations

import io
import os

import pytest

import test_stories_api as tsa
from clipping.aistory import thumbs
from test_stories_api import api  # noqa: F401 -- the route tests' app fixture, used as it is
from test_stories_api_list import _character


# ------------------------------------------------------------------ the rules

def test_the_thumbnail_is_named_after_its_original_and_lives_beside_it():
    assert thumbs.THUMB_WIDTH == 480  # re-pinned on purpose: 160 read soft on the list covers
    assert thumbs.thumb_name("portrait.png") == "portrait.png.thumb.jpg"
    assert thumbs.SIZES == ("thumb",)


@pytest.mark.parametrize("name,image", [
    ("portrait.png", True), ("portrait.JPG", True), ("variant_day.jpeg", True), ("image.webp", True),
    ("voice_sample.mp3", False), ("voice_sample.wav", False), ("portrait", False),
])
def test_only_an_image_has_a_thumbnail(name, image):
    assert thumbs.is_image_name(name) is image


def test_a_thumbnail_name_is_never_a_media_name_the_store_serves():
    """``portrait.png.thumb.jpg`` matches none of the store's name patterns,
    so the kept file is only ever reached through ``?size=thumb``."""
    from clipping.aistory import store as story_store

    for kind, patterns in story_store.MEDIA_NAME_PATTERNS.items():
        for pattern in patterns.values():
            for original in ("portrait.png", "image.png", "variant_day.png", "0" * 32 + ".png"):
                assert not pattern.fullmatch(thumbs.thumb_name(original)), (kind, original)


# ------------------------------------------------------------------ the route

def _png(width, height, colour=(200, 40, 90)):
    image_mod = pytest.importorskip("PIL.Image")
    buffer = io.BytesIO()
    image_mod.new("RGB", (width, height), colour).save(buffer, format="PNG")
    return buffer.getvalue()


def _story_with_portrait(api, body):
    story_id = tsa._chosen(api, style_template_id="fruit_drama")
    store = tsa._story_store(api)
    _character(store, story_id, "char_kiwi", "lead", portrait="portrait.png")
    refs = store.refs_dir(story_id, "characters", "char_kiwi", create=True)
    with open(os.path.join(refs, "portrait.png"), "wb") as fh:
        fh.write(body)
    return story_id, refs


def _url(story_id, name="portrait.png", kind="characters", eid="char_kiwi"):
    return f"/api/stories/{story_id}/media/{kind}/{eid}/{name}"


def test_the_route_serves_a_480_px_jpeg_and_keeps_it_beside_the_original(api):
    image_mod = pytest.importorskip("PIL.Image")
    original = _png(1080, 1920)
    story_id, refs = _story_with_portrait(api, original)

    response = api.client.get(_url(story_id), params={"size": "thumb"})

    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "image/jpeg"
    assert response.headers["cache-control"] == "no-store"
    served = image_mod.open(io.BytesIO(response.content))
    assert served.format == "JPEG" and served.size == (480, 853)
    assert len(response.content) < len(original)
    kept = os.path.join(refs, "portrait.png.thumb.jpg")
    assert os.path.isfile(kept) and not os.path.islink(kept)
    with open(kept, "rb") as fh:
        assert fh.read() == response.content
    # The original is untouched and still served as it was without the parameter.
    plain = api.client.get(_url(story_id))
    assert plain.status_code == 200 and plain.content == original
    assert plain.headers["content-type"] == "image/png"


def test_the_kept_thumbnail_is_reused_until_the_original_changes(api):
    pytest.importorskip("PIL.Image")
    story_id, refs = _story_with_portrait(api, _png(640, 800))
    kept = os.path.join(refs, "portrait.png.thumb.jpg")

    first = api.client.get(_url(story_id), params={"size": "thumb"}).content
    made = os.stat(kept)
    second = api.client.get(_url(story_id), params={"size": "thumb"}).content
    assert second == first
    assert os.stat(kept).st_ino == made.st_ino, "a current thumbnail is served, not made again"

    # A regenerated portrait reuses its name: the thumbnail follows it.
    portrait = os.path.join(refs, "portrait.png")
    with open(portrait, "wb") as fh:
        fh.write(_png(640, 800, colour=(10, 200, 30)))
    os.utime(portrait, ns=(made.st_mtime_ns + 5_000_000_000,) * 2)
    third = api.client.get(_url(story_id), params={"size": "thumb"}).content
    assert third != first


def test_a_small_image_is_never_enlarged(api):
    image_mod = pytest.importorskip("PIL.Image")
    story_id, _refs = _story_with_portrait(api, _png(100, 125))

    response = api.client.get(_url(story_id), params={"size": "thumb"})

    assert response.status_code == 200
    assert image_mod.open(io.BytesIO(response.content)).size == (100, 125)


@pytest.mark.parametrize("size", ["", "big", "THUMB", "thumb ", "160"])
def test_a_size_other_than_thumb_is_refused(api, size):
    story_id, refs = _story_with_portrait(api, b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)

    response = api.client.get(_url(story_id), params={"size": size})

    assert response.status_code == 400
    assert not os.path.lexists(os.path.join(refs, "portrait.png.thumb.jpg"))


def test_a_voice_sample_has_no_thumbnail(api):
    story_id, _refs = _story_with_portrait(api, b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    store = tsa._story_store(api)
    with open(os.path.join(store.entity_dir(story_id, "characters", "char_kiwi"), "voice_sample.mp3"), "wb") as fh:
        fh.write(b"ID3fake")

    response = api.client.get(_url(story_id, "voice_sample.mp3"), params={"size": "thumb"})

    assert response.status_code == 400


def test_the_original_name_rules_hold_with_the_parameter(api):
    story_id, _refs = _story_with_portrait(api, b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    for path in (
            _url(story_id, "portrait.png.thumb.jpg"),
            _url(story_id, "..%2F..%2Fstory.json"),
            _url(story_id, "turnaround.png"),
            _url(story_id, eid="char_nobody"),
            _url("0123456789ab"),
    ):
        assert api.client.get(path, params={"size": "thumb"}).status_code == 404, path


def test_a_symlinked_original_is_never_followed(api, tmp_path):
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 24)
    story_id, refs = _story_with_portrait(api, b"")
    portrait = os.path.join(refs, "portrait.png")
    os.unlink(portrait)
    os.symlink(outside, portrait)

    assert api.client.get(_url(story_id), params={"size": "thumb"}).status_code == 404
    assert not os.path.lexists(os.path.join(refs, "portrait.png.thumb.jpg"))


def test_a_symlink_where_the_thumbnail_goes_is_never_followed_nor_replaced(api, tmp_path):
    pytest.importorskip("PIL.Image")
    story_id, refs = _story_with_portrait(api, _png(320, 400))
    secret = tmp_path / "secret.jpg"
    secret.write_bytes(b"not yours")
    kept = os.path.join(refs, "portrait.png.thumb.jpg")
    os.symlink(secret, kept)

    response = api.client.get(_url(story_id), params={"size": "thumb"})

    assert response.status_code == 404
    assert b"not yours" not in response.content
    assert os.path.islink(kept) and os.readlink(kept) == str(secret)
    assert secret.read_bytes() == b"not yours"


def test_an_unreadable_image_falls_back_to_the_original(api):
    """A file Pillow cannot read (or no Pillow at all) is served as it is:
    a thumbnail is an optimisation, never a reason for a broken avatar."""
    body = b"\x89PNG\r\n\x1a\n" + b"\x00" * 24
    story_id, refs = _story_with_portrait(api, body)

    response = api.client.get(_url(story_id), params={"size": "thumb"})

    assert response.status_code == 200
    assert response.content == body and response.headers["content-type"] == "image/png"
    assert not os.path.lexists(os.path.join(refs, "portrait.png.thumb.jpg"))
    assert [name for name in os.listdir(refs) if name.startswith(".thumb-")] == []
