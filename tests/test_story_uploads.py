"""A character's design references: accepted safely, described by vision
(AI Story phase 2, stage 4; plan section 1, DEC-121).

``clipping/aistory/uploads.py`` imports Pillow only inside the functions that
decode, so this file collects and runs in the pytest-only CI environment
(DEC-012): every test that decodes an image asks ``pytest.importorskip("PIL")``
first; the size cap, the count cap, the missing-Pillow refusal, deletion and
everything about the vision description run everywhere.

Hermetic like ``tests/test_story_voices.py``: no key, chain, cap or limit of
the machine reaches a test, nothing is written outside ``tmp_path``, and no
real request leaves the process. The module is imported inside each test
(``_uploads()``), so on a commit without it every test fails on its own
instead of the file failing to collect.
"""

from __future__ import annotations

import copy
import hashlib
import io
import json
import os
import re
import struct
import sys
import zlib
from pathlib import Path

import pytest

from clipping.aistory import prompts, schemas
from clipping.aistory.store import StoryStore
from clipping.cancel import CancelToken
from clipping.providers.generation import GenResult
from clipping.providers.registry import Link
from clipping.providers.transport import Response

ROOT = Path(__file__).resolve().parents[1]
NOW = "2026-09-26T10:00:00+00:00"
MIB = 1024 * 1024

GEN_VARS = (
    "FAL_KEY", "OPENAI_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "POLLINATIONS_API_KEY",
    "GOOGLE_API_KEY", "OPENROUTER_API_KEY",
    "IMAGE_CHAIN", "IMAGE_EDIT_CHAIN", "VIDEO_CHAIN", "TTS_CHAIN", "VISION_CHAIN",
    "LOCAL_COMFYUI_URL", "LOCAL_OLLAMA_URL",
    "ALLOW_PAID", "PER_EPISODE_CAP_USD", "DAILY_CAP_USD", "PER_STORY_CAP_USD", "BUDGET_PROFILE",
    "LLM_CHAIN", "ALLOW_SLOW_CHAIN", "MAX_QUEUED_JOBS",
)
REAL_FILES = tuple(ROOT / "data" / name for name in ("usage.json", "spend.json", "chain_test_ledger.json"))
REAL_STORIES = (ROOT / "outputs" / "stories", ROOT / "outputs" / "stories.json")

GOOD_NOTES = "Round green kiwi body, fuzzy brown skin, tiny red scarf, big white sneakers, one leaf on top"
GOOD_REPLY = json.dumps({"appearance_notes": GOOD_NOTES})
KEYED_FLASH_LITE = {"VISION_CHAIN": "gemini/flash-lite", "GOOGLE_API_KEY": "test-key"}


def _uploads():
    from clipping.aistory import uploads

    return uploads


def _fingerprint(path: Path):
    if path.is_symlink() or path.exists():
        if path.is_file():
            return hashlib.sha256(path.read_bytes()).hexdigest()
        return "present"
    return None


@pytest.fixture(autouse=True)
def hermetic(monkeypatch, tmp_path):
    from clipping.config import PROVIDER_KEYS  # before the delenv: it reads .env
    from clipping.providers import adapters, budget, limits, transport

    for _name, (_attr, env_name) in PROVIDER_KEYS.items():
        monkeypatch.delenv(env_name, raising=False)
    for name in GEN_VARS:
        monkeypatch.delenv(name, raising=False)
    for name in list(os.environ):
        if name.startswith("LIMIT_"):
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("USAGE_PATH", str(tmp_path / "data" / "usage.json"))
    monkeypatch.setenv("SPEND_PATH", str(tmp_path / "data" / "spend.json"))
    limits.reset()
    budget.reset()
    adapters.load_all()

    def no_network(method, url, **_kwargs):
        raise AssertionError(f"a real request was attempted: {method} {url}")

    monkeypatch.setattr(transport, "urllib_transport", no_network)

    before = {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES}
    yield
    limits.reset()
    budget.reset()
    assert {path: _fingerprint(path) for path in REAL_FILES + REAL_STORIES} == before


@pytest.fixture
def store(tmp_path):
    return StoryStore(tmp_path / "outputs", on_log=lambda line: None)


class Log(list):
    def __call__(self, line):
        self.append(str(line))


# ------------------------------------------------------------------ helpers

def _char(char_id, *, name="Kiwi", uploads=()):
    return {
        "$schema": schemas.CHARACTER_SCHEMA_NAME,
        "char_id": char_id,
        "name": name,
        "role": "lead",
        "archetype": "x",
        "one_line": "x",
        "descriptor": None,
        "signature_items": [],
        "personality": {"traits": [], "wants": None, "fears": None, "speech_style": None},
        "relationships": {},
        "voice": None,
        "voice_hints": None,
        "refs": {"portrait": None, "turnaround": None, "expressions": None, "extra": [],
                 "uploads": [dict(entry) for entry in uploads]},
        "ref_seed": None,
        "prompt_block": None,
        "state": {"alive": True, "location": None, "arc_notes": []},
        "source": "custom",
        "approved_at": None,
        "created_at": NOW,
        "updated_at": NOW,
    }


def _story(store, *, route=None, uploads=()):
    profile = {"route": route} if route else None
    story_id = store.create(language="fr", seed_text="x", generation_profile=profile, now=NOW)["story_id"]
    store.write_entity(story_id, "characters", _char("char_kiwi", uploads=uploads), now=NOW)
    return story_id


def _entries(store, story_id):
    return store.read_entity(story_id, "characters", "char_kiwi")["refs"]["uploads"]


def _folder(store, story_id):
    return store.uploads_dir(story_id, "characters", "char_kiwi", create=True)


def _name(n):
    return f"{n:032x}.png"


def _png_chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)


def _stdlib_png(width, height, *, idat=None):
    """A valid RGB PNG built without Pillow (or, with *idat*, one whose header
    claims *width* x *height* over a tiny data stream: a pixel bomb)."""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    if idat is None:
        raw = b"".join(b"\x00" + b"\x10\x20\x30" * width for _ in range(height))
        idat = zlib.compress(raw)
    return b"\x89PNG\r\n\x1a\n" + _png_chunk(b"IHDR", ihdr) + _png_chunk(b"IDAT", idat) + _png_chunk(b"IEND", b"")


def _png_chunk_types(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    types, pos = [], 8
    while pos < len(data):
        length = struct.unpack(">I", data[pos:pos + 4])[0]
        types.append(data[pos + 4:pos + 8].decode("ascii"))
        pos += 12 + length
    return types


def _plant(store, story_id, tmp_path, name, *, description=None, data=None):
    """An accepted upload without Pillow: a file in refs/uploads/ and its entry."""
    src = tmp_path / f"plant-{name}"
    src.write_bytes(data if data is not None else _stdlib_png(4, 3))
    path = store.write_media(story_id, "characters", "char_kiwi", name, str(src))
    character = store.read_entity(story_id, "characters", "char_kiwi")
    character["refs"]["uploads"].append({"name": name, "description": description, "uploaded_at": NOW})
    store.write_entity(story_id, "characters", character, now=NOW)
    return path


class CountingStream:
    """*size* zero bytes, generated on demand; counts what was read."""

    def __init__(self, size):
        self.size = size
        self.read_bytes = 0
        self.reads = 0

    def read(self, n=-1):
        self.reads += 1
        left = self.size - self.read_bytes
        take = left if n is None or n < 0 else min(n, left)
        self.read_bytes += take
        return b"\x00" * take


class ExplodingStream:
    def read(self, n=-1):
        raise AssertionError("the body was read")


# Pillow-made fixtures ---------------------------------------------------------

def _pil():
    return pytest.importorskip("PIL")


def _image_bytes(fmt, size=(64, 48), mode="RGB", color=(200, 30, 30), **save):
    from PIL import Image

    buf = io.BytesIO()
    Image.new(mode, size, color).save(buf, fmt, **save)
    return buf.getvalue()


def _accept_bytes(store, story_id, data, **kwargs):
    return _uploads().accept_upload(store, story_id, "char_kiwi", io.BytesIO(data), now=NOW, **kwargs)


def _stored(store, story_id, name):
    return Path(store.media_path(story_id, "characters", "char_kiwi", name)).read_bytes()


# ----------------------------------------------------------- accept: images

def test_a_png_is_stored_as_a_uuid_png_that_decodes_and_carries_no_metadata(store):
    _pil()
    from PIL import Image, PngImagePlugin

    story_id = _story(store)
    info = PngImagePlugin.PngInfo()
    info.add_text("Comment", "secret-comment")
    info.add_itxt("Author", "secret-author", zip=True)
    data = _image_bytes("PNG", pnginfo=info, icc_profile=b"secret-icc-profile")
    assert b"secret-comment" in data

    entry = _accept_bytes(store, story_id, data)

    assert re.fullmatch(schemas.UPLOAD_NAME_PATTERN, entry["name"])
    assert entry == {"name": entry["name"], "description": None, "uploaded_at": NOW}
    assert _entries(store, story_id) == [entry]
    stored = _stored(store, story_id, entry["name"])
    assert _png_chunk_types(stored) == ["IHDR", "IDAT", "IEND"]
    assert b"secret" not in stored
    with Image.open(io.BytesIO(stored)) as image:
        assert (image.format, image.size, image.mode) == ("PNG", (64, 48), "RGB")
        assert image.getpixel((5, 5)) == (200, 30, 30)
    # Nothing else in the folder: no temp file outlives a success.
    assert os.listdir(_folder(store, story_id)) == [entry["name"]]


def test_a_jpeg_with_exif_loses_it_and_is_turned_upright(store):
    _pil()
    from PIL import Image

    story_id = _story(store)
    exif = Image.Exif()
    exif[0x010F] = "SpyCam"        # Make
    exif[0x0131] = "SecretApp 1.0"  # Software
    exif[0x0112] = 6               # Orientation: rotate 90 degrees clockwise to view
    data = _image_bytes("JPEG", size=(64, 48), exif=exif.tobytes())
    assert b"SpyCam" in data

    entry = _accept_bytes(store, story_id, data)

    stored = _stored(store, story_id, entry["name"])
    assert b"SpyCam" not in stored and b"SecretApp" not in stored
    assert _png_chunk_types(stored) == ["IHDR", "IDAT", "IEND"]
    with Image.open(io.BytesIO(stored)) as image:
        assert image.size == (48, 64)  # upright
        assert dict(image.getexif()) == {}


def test_two_uploads_with_the_same_file_name_are_two_files_both_kept(store, tmp_path):
    _pil()
    uploads = _uploads()
    story_id = _story(store)
    first_dir, second_dir = tmp_path / "a", tmp_path / "b"
    first_dir.mkdir()
    second_dir.mkdir()
    (first_dir / "talk.png").write_bytes(_image_bytes("PNG", color=(1, 2, 3)))
    (second_dir / "talk.png").write_bytes(_image_bytes("PNG", color=(4, 5, 6)))

    one = uploads.accept_upload(store, story_id, "char_kiwi", str(first_dir / "talk.png"), now=NOW)
    with open(second_dir / "talk.png", "rb") as fh:
        two = uploads.accept_upload(store, story_id, "char_kiwi", fh, now=NOW)

    assert one["name"] != two["name"]
    assert "talk" not in one["name"] and "talk" not in two["name"]
    assert [e["name"] for e in _entries(store, story_id)] == [one["name"], two["name"]]
    assert sorted(os.listdir(_folder(store, story_id))) == sorted([one["name"], two["name"]])
    assert _stored(store, story_id, one["name"]) != _stored(store, story_id, two["name"])


def test_a_gif_keeps_its_first_frame(store):
    _pil()
    from PIL import Image

    story_id = _story(store)
    first = Image.new("RGB", (16, 16), (255, 0, 0))
    second = Image.new("RGB", (16, 16), (0, 0, 255))
    buf = io.BytesIO()
    first.save(buf, "GIF", save_all=True, append_images=[second], duration=100, loop=0)

    entry = _accept_bytes(store, story_id, buf.getvalue())

    with Image.open(io.BytesIO(_stored(store, story_id, entry["name"]))) as image:
        assert image.format == "PNG" and getattr(image, "n_frames", 1) == 1
        assert image.convert("RGB").getpixel((8, 8)) == (255, 0, 0)


def test_a_webp_is_accepted(store):
    _pil()
    from PIL import Image, features

    if not features.check("webp"):
        pytest.skip("this Pillow has no WebP support")
    story_id = _story(store)
    entry = _accept_bytes(store, story_id, _image_bytes("WEBP", lossless=True))
    with Image.open(io.BytesIO(_stored(store, story_id, entry["name"]))) as image:
        assert (image.format, image.size) == ("PNG", (64, 48))


def test_a_multi_picture_jpeg_is_accepted_as_a_jpeg(store):
    """Many phone cameras write MPO; Pillow's JPEG decoder reports it as such."""
    _pil()
    from PIL import Image

    story_id = _story(store)
    buf = io.BytesIO()
    Image.new("RGB", (32, 24), (10, 200, 10)).save(
        buf, "MPO", save_all=True, append_images=[Image.new("RGB", (32, 24), (0, 0, 0))])
    with Image.open(io.BytesIO(buf.getvalue()), formats=("JPEG",)) as probe:
        assert probe.format == "MPO"

    entry = _accept_bytes(store, story_id, buf.getvalue())
    with Image.open(io.BytesIO(_stored(store, story_id, entry["name"]))) as image:
        assert image.size == (32, 24)


def test_transparency_is_kept_as_rgba_and_everything_else_is_rgb(store):
    _pil()
    from PIL import Image

    story_id = _story(store)
    rgba = _accept_bytes(store, story_id, _image_bytes("PNG", mode="RGBA", color=(1, 2, 3, 0)))
    palette = Image.new("P", (8, 8), 0)
    palette.putpalette([0, 0, 0, 255, 255, 255] * 128)
    buf = io.BytesIO()
    palette.save(buf, "PNG", transparency=0)
    keyed = _accept_bytes(store, story_id, buf.getvalue())
    grey = _accept_bytes(store, story_id, _image_bytes("PNG", mode="L", color=128))

    modes = []
    for entry in (rgba, keyed, grey):
        with Image.open(io.BytesIO(_stored(store, story_id, entry["name"]))) as image:
            modes.append(image.mode)
            if image.mode == "RGBA":
                assert image.getpixel((1, 1))[3] == 0
    assert modes == ["RGBA", "RGBA", "RGB"]


def test_a_large_image_is_scaled_down_to_max_side(store):
    _pil()
    from PIL import Image

    uploads = _uploads()
    story_id = _story(store)
    entry = _accept_bytes(store, story_id, _image_bytes("PNG", size=(3000, 600)))
    with Image.open(io.BytesIO(_stored(store, story_id, entry["name"]))) as image:
        assert max(image.size) == uploads.MAX_SIDE
        assert image.size == (uploads.MAX_SIDE, 307)


def test_a_body_of_exactly_max_bytes_passes_the_size_cap(store):
    _pil()
    story_id = _story(store)
    data = _image_bytes("PNG")
    entry = _accept_bytes(store, story_id, data, max_bytes=len(data))
    assert _entries(store, story_id) == [entry]


# ------------------------------------------------------ accept: refusals

def _assert_nothing_left(store, story_id):
    assert os.listdir(_folder(store, story_id)) == []
    assert _entries(store, story_id) == []


def test_a_text_file_named_png_is_not_an_image_and_leaves_nothing(store, tmp_path):
    _pil()
    uploads = _uploads()
    story_id = _story(store)
    fake = tmp_path / "x.png"
    fake.write_text("hello, this is not a picture\n" * 20)

    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.accept_upload(store, story_id, "char_kiwi", str(fake), now=NOW)

    assert excinfo.value.code == "not_an_image" and excinfo.value.http_status == 415
    _assert_nothing_left(store, story_id)


@pytest.mark.parametrize("fmt", ["PNG", "JPEG"])
def test_a_truncated_image_is_refused(store, fmt):
    _pil()
    uploads = _uploads()
    story_id = _story(store)
    from PIL import Image

    buf = io.BytesIO()
    Image.effect_noise((256, 256), 64).convert("RGB").save(buf, fmt)
    data = buf.getvalue()

    with pytest.raises(uploads.UploadError) as excinfo:
        _accept_bytes(store, story_id, data[: len(data) // 2])

    assert excinfo.value.code == "not_an_image"
    _assert_nothing_left(store, story_id)


@pytest.mark.parametrize("data", [
    b'<?xml version="1.0"?><svg xmlns="http://www.w3.org/2000/svg" width="10" height="10">'
    b'<rect width="10" height="10" fill="red"/></svg>',
    b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\ntrailer << /Root 1 0 R >>\n%%EOF\n",
], ids=["svg", "pdf"])
def test_svg_and_pdf_are_not_images(store, data):
    _pil()
    uploads = _uploads()
    story_id = _story(store)
    with pytest.raises(uploads.UploadError) as excinfo:
        _accept_bytes(store, story_id, data)
    assert excinfo.value.code == "not_an_image"
    _assert_nothing_left(store, story_id)


@pytest.mark.parametrize("fmt, label", [("BMP", "BMP"), ("TIFF", "TIFF")])
def test_another_image_format_is_unsupported_and_never_decoded(store, monkeypatch, fmt, label):
    _pil()
    from PIL import Image

    uploads = _uploads()
    story_id = _story(store)
    data = _image_bytes(fmt)
    real_open = Image.open
    asked = []

    def spy_open(fp, mode="r", formats=None):
        asked.append(formats)
        return real_open(fp, mode, formats)

    monkeypatch.setattr(Image, "open", spy_open)
    with pytest.raises(uploads.UploadError) as excinfo:
        _accept_bytes(store, story_id, data)

    assert excinfo.value.code == "unsupported_format" and excinfo.value.http_status == 415
    assert label in str(excinfo.value)
    # Only ever opened with the four allowed decoders.
    assert asked and all(tuple(formats) == uploads.ALLOWED_FORMATS for formats in asked)
    _assert_nothing_left(store, story_id)


def test_an_empty_body_is_not_an_image(store):
    uploads = _uploads()
    story_id = _story(store)
    with pytest.raises(uploads.UploadError) as excinfo:
        _accept_bytes(store, story_id, b"")
    assert excinfo.value.code == "not_an_image"
    _assert_nothing_left(store, story_id)


def test_an_11_mib_stream_is_too_large_without_being_read_to_the_end(store):
    uploads = _uploads()
    story_id = _story(store)
    stream = CountingStream(11 * MIB)

    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.accept_upload(store, story_id, "char_kiwi", stream, now=NOW)

    assert uploads.MAX_UPLOAD_BYTES == 10 * MIB
    assert excinfo.value.code == "too_large" and excinfo.value.http_status == 413
    assert "10 MB" in str(excinfo.value)
    assert stream.read_bytes == 10 * MIB + 1   # one byte past the cap, never more
    assert stream.read_bytes < stream.size
    assert stream.reads == 11                  # ten 1 MiB chunks, then a single byte
    _assert_nothing_left(store, story_id)


def test_the_size_cap_is_a_parameter(store):
    uploads = _uploads()
    story_id = _story(store)
    stream = CountingStream(101)
    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.accept_upload(store, story_id, "char_kiwi", stream, now=NOW, max_bytes=100)
    assert excinfo.value.code == "too_large" and stream.read_bytes == 101
    with pytest.raises(ValueError):
        uploads.accept_upload(store, story_id, "char_kiwi", CountingStream(1), now=NOW, max_bytes=0)
    _assert_nothing_left(store, story_id)


def test_a_fifth_upload_is_refused_before_the_body_is_read(store):
    uploads = _uploads()
    four = [{"name": _name(n), "description": None, "uploaded_at": NOW} for n in range(1, 5)]
    story_id = _story(store, uploads=four)
    assert uploads.MAX_UPLOADS == schemas.MAX_UPLOADS == 4

    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.accept_upload(store, story_id, "char_kiwi", ExplodingStream(), now=NOW)

    assert excinfo.value.code == "too_many" and excinfo.value.http_status == 400
    assert _entries(store, story_id) == four


def test_without_pillow_an_upload_is_refused_and_leaves_nothing(store, monkeypatch):
    uploads = _uploads()
    story_id = _story(store)
    for name in [n for n in sys.modules if n == "PIL" or n.startswith("PIL.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "PIL", None)  # "import PIL" now fails

    with pytest.raises(uploads.UploadError) as excinfo:
        _accept_bytes(store, story_id, _stdlib_png(4, 3))

    assert excinfo.value.code == "unavailable" and excinfo.value.http_status == 503
    _assert_nothing_left(store, story_id)


def test_a_symlinked_uploads_folder_is_refused_before_reading(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    refs = Path(store.refs_dir(story_id, "characters", "char_kiwi", create=True))
    os.symlink(elsewhere, refs / "uploads")

    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.accept_upload(store, story_id, "char_kiwi", ExplodingStream(), now=NOW)

    assert excinfo.value.code == "storage"
    assert os.listdir(elsewhere) == []


def test_an_unknown_story_or_character_is_a_key_error(store):
    uploads = _uploads()
    story_id = _story(store)
    for sid, cid in ((story_id, "char_nobody"), ("0123456789ab", "char_kiwi"), (story_id, "../x")):
        with pytest.raises(KeyError):
            uploads.accept_upload(store, sid, cid, ExplodingStream(), now=NOW)


def test_error_codes_map_to_http_statuses():
    uploads = _uploads()
    status = {code: uploads.UploadError("x", code=code).http_status
              for code in ("too_large", "too_many", "not_an_image", "unsupported_format")}
    assert status == {"too_large": 413, "too_many": 400, "not_an_image": 415, "unsupported_format": 415}


# ------------------------------------------------ accept: decompression bombs

def test_a_pixel_bomb_header_is_refused_before_load(store, monkeypatch):
    _pil()
    from PIL import Image, PngImagePlugin

    uploads = _uploads()
    story_id = _story(store)
    before = Image.MAX_IMAGE_PIXELS

    def no_load(self, *args, **kwargs):
        raise AssertionError("load() was reached")

    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", no_load)
    bomb = _stdlib_png(20000, 20000, idat=zlib.compress(b"\x00" * 64))

    with pytest.raises(uploads.UploadError) as excinfo:
        _accept_bytes(store, story_id, bomb)

    assert excinfo.value.code == "too_large" and "megapixels" in str(excinfo.value)
    assert Image.MAX_IMAGE_PIXELS == before
    _assert_nothing_left(store, story_id)


@pytest.mark.parametrize("preset", ["default", None], ids=["pillow-default", "pillow-limit-disabled"])
def test_a_header_between_the_limit_and_twice_it_is_refused(store, monkeypatch, preset):
    """7000 x 7000 = 49 MP: over MAX_PIXELS, under Pillow's own error level at
    its default. Pillow's check runs with its limit held at MAX_PIXELS (even
    when the process had switched it off) and its warning is the refusal, at
    ``open()`` -- before ``verify()``, before the explicit check's message."""
    _pil()
    from PIL import Image, PngImagePlugin

    uploads = _uploads()
    story_id = _story(store)
    if preset is None:
        monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", None)
    before = Image.MAX_IMAGE_PIXELS
    real_check = Image._decompression_bomb_check
    limits_seen = []

    def spy_check(size):
        limits_seen.append(Image.MAX_IMAGE_PIXELS)
        return real_check(size)

    def no_load(self, *args, **kwargs):
        raise AssertionError("load() was reached")

    def no_verify(self, *args, **kwargs):
        raise AssertionError("verify() was reached")

    monkeypatch.setattr(Image, "_decompression_bomb_check", spy_check)
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", no_load)
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "verify", no_verify)

    with pytest.raises(uploads.UploadError) as excinfo:
        _accept_bytes(store, story_id, _stdlib_png(7000, 7000, idat=zlib.compress(b"\x00" * 64)))

    assert excinfo.value.code == "too_large"
    assert "7000x7000" not in str(excinfo.value)  # Pillow's refusal, not the explicit check's
    assert limits_seen == [uploads.MAX_PIXELS]
    assert Image.MAX_IMAGE_PIXELS == before
    _assert_nothing_left(store, story_id)


def test_the_explicit_pixel_check_holds_without_pillows_own(store, monkeypatch):
    _pil()
    from PIL import Image, PngImagePlugin

    uploads = _uploads()
    story_id = _story(store)
    monkeypatch.setattr(Image, "_decompression_bomb_check", lambda size: None)
    loads = []
    monkeypatch.setattr(PngImagePlugin.PngImageFile, "load", lambda self, *a, **k: loads.append(1))

    with pytest.raises(uploads.UploadError) as excinfo:
        _accept_bytes(store, story_id, _stdlib_png(20000, 20000, idat=zlib.compress(b"\x00" * 64)))

    assert excinfo.value.code == "too_large" and "20000x20000" in str(excinfo.value)
    assert loads == []
    _assert_nothing_left(store, story_id)


# ------------------------------------------------------------------ delete

def test_delete_upload_removes_the_file_and_the_entry(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    kept = _plant(store, story_id, tmp_path, _name(1), description="a red scarf")
    gone = _plant(store, story_id, tmp_path, _name(2))

    report = uploads.delete_upload(store, story_id, "char_kiwi", _name(2), now=NOW)

    assert report == {"name": _name(2), "entry_removed": True, "file_removed": True}
    assert not os.path.lexists(gone) and os.path.isfile(kept)
    assert [e["name"] for e in _entries(store, story_id)] == [_name(1)]


@pytest.mark.parametrize("name", [
    "../character.json", "../../story.json", "/etc/passwd", "x.png", _name(1).upper(), _name(1)[:-4] + ".jpg",
    _name(1) + "\n", "", None, f"sub/{_name(1)}",
])
def test_delete_upload_refuses_a_bad_name_and_touches_nothing(store, tmp_path, name):
    uploads = _uploads()
    story_id = _story(store)
    path = _plant(store, story_id, tmp_path, _name(1))
    story_json = Path(store.story_dir(story_id)) / "story.json"
    before = story_json.read_bytes()

    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.delete_upload(store, story_id, "char_kiwi", name, now=NOW)

    assert excinfo.value.code == "bad_name"
    assert os.path.isfile(path) and [e["name"] for e in _entries(store, story_id)] == [_name(1)]
    assert story_json.read_bytes() == before


def test_delete_upload_refuses_a_symlink_and_keeps_its_target_and_entry(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    _plant(store, story_id, tmp_path, _name(1))
    target = tmp_path / "precious.png"
    target.write_bytes(b"do not delete")
    folder = Path(_folder(store, story_id))
    os.unlink(folder / _name(1))
    os.symlink(target, folder / _name(1))

    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.delete_upload(store, story_id, "char_kiwi", _name(1), now=NOW)

    assert excinfo.value.code == "storage" and excinfo.value.http_status == 409
    assert target.read_bytes() == b"do not delete"
    assert os.path.islink(folder / _name(1))
    assert [e["name"] for e in _entries(store, story_id)] == [_name(1)]


def test_delete_upload_never_goes_through_a_symlinked_folder(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    _plant(store, story_id, tmp_path, _name(1))
    refs = Path(store.refs_dir(story_id, "characters", "char_kiwi"))
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / _name(1)).write_bytes(b"outside")
    os.rename(refs / "uploads", tmp_path / "moved-uploads")
    os.symlink(elsewhere, refs / "uploads")

    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.delete_upload(store, story_id, "char_kiwi", _name(1), now=NOW)

    assert excinfo.value.code == "storage"
    assert (elsewhere / _name(1)).read_bytes() == b"outside"
    assert [e["name"] for e in _entries(store, story_id)] == [_name(1)]


def test_delete_upload_of_an_unknown_name_is_not_found(store):
    uploads = _uploads()
    story_id = _story(store)
    with pytest.raises(uploads.UploadError) as excinfo:
        uploads.delete_upload(store, story_id, "char_kiwi", _name(7), now=NOW)
    assert excinfo.value.code == "not_found" and excinfo.value.http_status == 404


def test_delete_upload_removes_an_entry_whose_file_is_gone(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    os.unlink(_plant(store, story_id, tmp_path, _name(1)))
    report = uploads.delete_upload(store, story_id, "char_kiwi", _name(1), now=NOW)
    assert report == {"name": _name(1), "entry_removed": True, "file_removed": False}
    assert _entries(store, story_id) == []


# ---------------------------------------------------------------- describe

class FakeVision:
    """A vision adapter answering *replies* in turn (the last one repeats);
    keeps every request. Its estimate is the real Gemini adapter's."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests = []

    def estimate(self, link, request):
        from clipping.providers import vision

        return vision.GEMINI_VISION.estimate(link, request)

    def probe(self, link, **_):
        return True, "ok"

    def generate(self, link, request, *, credentials, on_log, transport=None, **_):
        self.requests.append(copy.copy(request))
        text = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
        return GenResult(provider=link.provider, model=link.model, paths=(), meta={"text": text})


class CountingTransport:
    def __init__(self):
        self.calls = []

    def __call__(self, method, url, **kwargs):
        self.calls.append((method, url))
        raise AssertionError(f"unexpected request {method} {url}")


def _ledger(store, story_id):
    path = Path(store.story_dir(story_id)) / "cost_ledger.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))["entries"]


def _describe(store, story_id, name, *, env, adapters, log=None, transport=None):
    return _uploads().describe_upload(store, story_id, "char_kiwi", name, env=env,
                                      on_log=log if log is not None else Log(),
                                      cancel=CancelToken(), adapters=adapters, transport=transport)


def test_describe_upload_stores_the_notes_and_books_one_free_call(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    path = _plant(store, story_id, tmp_path, _name(1))
    fake = FakeVision(f"Here you go:\n```json\n{GOOD_REPLY}\n```")
    log = Log()

    notes = _describe(store, story_id, _name(1), env=KEYED_FLASH_LITE, adapters={("vision", "gemini"): fake},
                      log=log)

    assert notes == GOOD_NOTES
    assert _entries(store, story_id)[0]["description"] == GOOD_NOTES
    assert len(fake.requests) == 1
    request = fake.requests[0]
    system, user, _schema = prompts.build_u1(language="fr")
    assert request.kind == "vision" and system in request.prompt and user in request.prompt
    assert "appearance_notes" in request.prompt
    assert request.images == (path,)
    assert request.extra == {"max_tokens": prompts.MAX_TOKENS["U1"], "temperature": prompts.TEMPERATURE["U1"]}
    rows = _ledger(store, story_id)
    assert [(r["step"], r["provider"], r["model"], r["unit"], r["est_usd"], r["paid"]) for r in rows] == [
        ("upload_description", "gemini", "gemini-3.5-flash-lite", "token", 0.0, False)]
    assert rows[0]["qty"] == uploads._vision_tokens(request) > 258
    assert log[-1] == "👁 Kiwi: design reference described via gemini/flash-lite"
    assert not (tmp_path / "data" / "spend.json").exists()


def test_describe_upload_asks_once_more_after_a_rejected_reply(store, tmp_path):
    story_id = _story(store)
    _plant(store, story_id, tmp_path, _name(1))
    fake = FakeVision("I cannot see any JSON here", GOOD_REPLY)
    log = Log()

    notes = _describe(store, story_id, _name(1), env=KEYED_FLASH_LITE, adapters={("vision", "gemini"): fake},
                      log=log)

    assert notes == GOOD_NOTES and len(fake.requests) == 2
    assert fake.requests[0].prompt == fake.requests[1].prompt
    assert any(line.startswith("⚠️ U1 reply rejected") for line in log)
    assert len(_ledger(store, story_id)) == 2  # both answers booked


@pytest.mark.parametrize("second", [
    json.dumps({"appearance_notes": " ".join(["word"] * 41)}),
    json.dumps({"appearance_notes": " ".join(["x" * 30] * 14)}),  # 14 words, over 400 characters
    json.dumps({"appearance_notes": "fine", "extra": 1}),
], ids=["too-many-words", "too-long-for-the-schema", "extra-key"])
def test_describe_upload_rejected_twice_stores_nothing(store, tmp_path, second):
    uploads = _uploads()
    story_id = _story(store)
    _plant(store, story_id, tmp_path, _name(1))
    before = store.read_entity(story_id, "characters", "char_kiwi")
    fake = FakeVision("not json at all", second)

    with pytest.raises(uploads.UploadError) as excinfo:
        _describe(store, story_id, _name(1), env=KEYED_FLASH_LITE, adapters={("vision", "gemini"): fake})

    assert excinfo.value.code == "could_not_describe" and excinfo.value.http_status == 502
    assert excinfo.value.reasons and all("appearance_notes" in r or "$" in r for r in excinfo.value.reasons)
    assert len(fake.requests) == 2
    assert store.read_entity(story_id, "characters", "char_kiwi") == before
    assert len(_ledger(store, story_id)) == 2


def test_describe_upload_with_no_runnable_link_names_every_reason(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    _plant(store, story_id, tmp_path, _name(1))
    chain = "gemini/flash-lite,openrouter/qwen/qwen3.8-27b:free"
    fake = FakeVision(GOOD_REPLY)

    with pytest.raises(uploads.UploadError) as excinfo:
        _describe(store, story_id, _name(1), env={"VISION_CHAIN": chain},
                  adapters={("vision", "gemini"): fake, ("vision", "openrouter"): fake})

    assert excinfo.value.code == "vision_unavailable" and excinfo.value.http_status == 503
    assert excinfo.value.reasons == [
        "gemini/flash-lite: no API key (GOOGLE_API_KEY is not set)",
        "openrouter/qwen/qwen3.8-27b:free: no API key (OPENROUTER_API_KEY is not set)",
    ]
    assert fake.requests == [] and _ledger(store, story_id) == []
    assert _entries(store, story_id)[0]["description"] is None


def test_describe_upload_never_reaches_a_paid_link_while_allow_paid_is_off(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    _plant(store, story_id, tmp_path, _name(1))
    fake, transport = FakeVision(GOOD_REPLY), CountingTransport()

    with pytest.raises(uploads.UploadError) as excinfo:
        _describe(store, story_id, _name(1), env={"VISION_CHAIN": "gemini/flash", "GOOGLE_API_KEY": "test-key"},
                  adapters={("vision", "gemini"): fake}, transport=transport)

    assert excinfo.value.code == "vision_unavailable"
    [reason] = excinfo.value.reasons
    assert reason.startswith("gemini/flash: paid link; allow_paid is off (est $")
    assert "daily cap" in reason
    assert fake.requests == [] and transport.calls == []
    assert _ledger(store, story_id) == []
    assert not (tmp_path / "data" / "spend.json").exists()


def test_describe_upload_books_a_paid_link_once_allow_paid_is_on(store, tmp_path):
    from clipping.providers import budget

    story_id = _story(store)
    _plant(store, story_id, tmp_path, _name(1))
    fake = FakeVision(GOOD_REPLY)
    log = Log()

    notes = _describe(store, story_id, _name(1),
                      env={"VISION_CHAIN": "gemini/flash", "GOOGLE_API_KEY": "test-key", "ALLOW_PAID": "1"},
                      adapters={("vision", "gemini"): fake}, log=log)

    assert notes == GOOD_NOTES and len(fake.requests) == 1
    [row] = _ledger(store, story_id)
    expected = FakeVision().estimate(Link("gemini", "flash"), fake.requests[0])
    assert (row["step"], row["unit"], row["qty"], row["paid"]) == ("upload_description", "token", expected.qty, True)
    assert row["est_usd"] == round(expected.est_usd, 4)
    assert any(line.startswith("   💸 gemini/flash: est $") for line in log)
    assert (tmp_path / "data" / "spend.json").exists()
    assert budget.day_spent() == pytest.approx(expected.est_usd)


def test_describe_upload_through_the_real_ollama_adapter(store, tmp_path):
    """The reply's path end to end: Ollama's /api/chat answer -> GenResult.meta["text"] -> U1."""
    import base64

    story_id = _story(store)
    data = _stdlib_png(4, 3)
    _plant(store, story_id, tmp_path, _name(1), data=data)
    calls = []

    def transport(method, url, *, headers=None, body=None, timeout=60):
        calls.append((method, url, body))
        if url == "http://ollama.test/api/tags":
            return Response(200, {}, json.dumps({"models": [{"name": "gemma3:4b"}]}).encode())
        if url == "http://ollama.test/api/chat":
            return Response(200, {}, json.dumps({"message": {"content": GOOD_REPLY},
                                                 "prompt_eval_count": 300, "eval_count": 30}).encode())
        raise AssertionError(f"unexpected request {method} {url}")

    notes = _describe(store, story_id, _name(1),
                      env={"VISION_CHAIN": "local/ollama-vision", "LOCAL_OLLAMA_URL": "http://ollama.test"},
                      adapters=None, transport=transport)

    assert notes == GOOD_NOTES
    chat = json.loads([c for c in calls if c[1].endswith("/api/chat")][0][2])
    [message] = chat["messages"]
    assert prompts.build_u1(language="fr")[1] in message["content"]
    assert message["images"] == [base64.b64encode(data).decode("ascii")]
    [row] = _ledger(store, story_id)
    assert (row["provider"], row["unit"], row["est_usd"], row["paid"]) == ("local", "token", 0.0, False)


def test_describe_upload_on_route_local_never_calls_an_api_link(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store, route="local")
    _plant(store, story_id, tmp_path, _name(1))
    api = FakeVision(GOOD_REPLY)

    with pytest.raises(uploads.UploadError) as excinfo:
        _describe(store, story_id, _name(1), env=KEYED_FLASH_LITE, adapters={("vision", "gemini"): api})

    assert excinfo.value.reasons == ["gemini/flash-lite: route is local"]
    assert "route local" in str(excinfo.value)
    assert api.requests == []


def test_describe_upload_refuses_a_bad_or_unknown_name(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    fake = FakeVision(GOOD_REPLY)
    for name, code in (("../story.json", "bad_name"), (_name(9), "not_found")):
        with pytest.raises(uploads.UploadError) as excinfo:
            _describe(store, story_id, name, env=KEYED_FLASH_LITE, adapters={("vision", "gemini"): fake})
        assert excinfo.value.code == code
    # Listed, but its file is gone (or was never a regular file).
    os.unlink(_plant(store, story_id, tmp_path, _name(1)))
    with pytest.raises(uploads.UploadError) as excinfo:
        _describe(store, story_id, _name(1), env=KEYED_FLASH_LITE, adapters={("vision", "gemini"): fake})
    assert excinfo.value.code == "not_found"
    assert fake.requests == []


def test_describe_upload_refuses_an_unreadable_ledger_before_any_call(store, tmp_path):
    uploads = _uploads()
    story_id = _story(store)
    _plant(store, story_id, tmp_path, _name(1))
    (Path(store.story_dir(story_id)) / "cost_ledger.json").write_text("{torn")
    fake = FakeVision(GOOD_REPLY)

    with pytest.raises(uploads.UploadError) as excinfo:
        _describe(store, story_id, _name(1), env=KEYED_FLASH_LITE, adapters={("vision", "gemini"): fake})

    assert excinfo.value.code == "config" and fake.requests == []


# ------------------------------------------------------------- upload_notes

def test_upload_notes_joins_the_descriptions_for_k1():
    uploads = _uploads()
    assert uploads.upload_notes(_char("char_kiwi")) is None
    assert uploads.upload_notes(_char("char_kiwi", uploads=[
        {"name": _name(1), "description": None, "uploaded_at": NOW}])) is None
    character = _char("char_kiwi", uploads=[
        {"name": _name(1), "description": "Round green body, red scarf.", "uploaded_at": NOW},
        {"name": _name(2), "description": None, "uploaded_at": NOW},
        {"name": _name(3), "description": "  White   sneakers ", "uploaded_at": NOW},
    ])
    notes = uploads.upload_notes(character)
    assert notes == "Round green body, red scarf; White sneakers"


def test_the_description_cap_is_the_schemas():
    uploads = _uploads()
    upload_schema = schemas.CHARACTER_SCHEMA["properties"]["refs"]["properties"]["uploads"]["items"]
    assert uploads.DESCRIPTION_MAX_CHARS == upload_schema["properties"]["description"]["maxLength"]
    assert uploads.UPLOAD_NAME.pattern == schemas.UPLOAD_NAME_PATTERN
