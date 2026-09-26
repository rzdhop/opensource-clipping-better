"""A character's design references: accepted safely, described by vision
(AI Story phase 2, stage 4; plan section 1, DEC-121).

A user may give a character up to ``schemas.MAX_UPLOADS`` design references
(stylised characters only -- never a way to imitate a real person, DEC-118).
Each one is untrusted input, so :func:`accept_upload`:

- refuses a fifth upload **before** reading a byte of the body;
- streams the body in 1 MiB chunks into a hidden temp file inside the
  character's own ``refs/uploads/`` (``StoryStore.uploads_dir``) and stops
  as soon as it passes ``MAX_UPLOAD_BYTES`` -- it never reads more than one
  byte past the cap, and the partial file is removed;
- decodes it with Pillow, and only with the PNG, JPEG, WebP and GIF decoders
  (``Image.open(formats=...)``): open -> ``verify()`` -> reopen -> the pixel
  count checked from the header **before** ``load()`` (the decompression-bomb
  guard: ``MAX_PIXELS``, plus Pillow's own ``MAX_IMAGE_PIXELS`` held at the
  same limit and its ``DecompressionBombWarning`` turned into a refusal) ->
  ``load()`` (a GIF, animated WebP or APNG keeps its first frame) -> EXIF
  orientation applied -> RGB or RGBA -> at most ``MAX_SIDE`` pixels on its
  long side -> saved as a PNG carrying no metadata at all (no EXIF, no ICC
  profile, no text chunks: only IHDR, IDAT and IEND);
- names it ``<uuid4 hex>.png`` (``schemas.UPLOAD_NAME_PATTERN``), so two
  uploads of the same file name are two files -- the original file name is
  never used on disk, nor anywhere else;
- appends ``{name, description: null, uploaded_at}`` to the character's
  ``refs.uploads`` (``StoryStore.write_entity``).

Anything Pillow cannot decode -- a text file renamed ``.png``, a truncated
file, SVG, PDF -- is ``not_an_image``; an image in another format (BMP,
TIFF, HEIC...) is ``unsupported_format``. :class:`UploadError` carries the
short sentence for the user and a ``code`` the API maps to an HTTP status
(:data:`HTTP_STATUS`).

:func:`describe_upload` runs prompt U1 through ``VISION_CHAIN`` with every
gate the generation runner has (route, keys, local probe, ``allow_paid`` and
the budget for a paid link, the free-tier limiter), books each answered call
in the story's ledger, and stores the appearance notes as the upload's
``description``; :func:`upload_notes` joins those for K1, which folds them
into the character's descriptor.

Pillow is a dependency of the application, not of the pytest-only CI
environment (DEC-012): it is imported inside the functions that decode, so
this module imports with the standard library alone.
"""

from __future__ import annotations

import copy
import json
import os
import re
import tempfile
import threading
import uuid
import warnings
from datetime import datetime, timezone

from clipping.providers import adapters as adapters_mod
from clipping.providers import budget as budget_mod
from clipping.providers import gating, jsonx
from clipping.providers import generation as gen
from clipping.providers.registry import ChainError, describe

from . import ledger as ledger_mod
from . import prompts, schemas
from . import store as store_mod

KIND = "characters"
STEP = "upload_description"
LEDGER_NAME = "cost_ledger.json"
PROMPT_ID = "U1"

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_UPLOADS = schemas.MAX_UPLOADS
# Width x height, read from the header before anything is decoded. 40 MP is
# a large phone photo; an RGBA image this size decodes to 160 MB.
MAX_PIXELS = 40_000_000
# The stored reference's long side. A design reference needs no more, and a
# full-size PNG of a phone photo (tens of MB) would not fit in a vision
# request as a data URL.
MAX_SIDE = 1536
CHUNK_BYTES = 1024 * 1024

# The only decoders Pillow is allowed to run on an upload.
ALLOWED_FORMATS = ("PNG", "JPEG", "WEBP", "GIF")
# What those decoders may report: Pillow's JPEG decoder opens a multi-picture
# JPEG (many phone cameras write one) as "MPO"; its first picture is a JPEG.
_DECODED_FORMATS = frozenset(ALLOWED_FORMATS) | {"MPO"}

# schemas.CHARACTER_SCHEMA refs.uploads[].description.maxLength
DESCRIPTION_MAX_CHARS = 400

UPLOAD_NAME = re.compile(schemas.UPLOAD_NAME_PATTERN)

_TEMP_PREFIX = ".upload-"
_NAME_ATTEMPTS = 8

HTTP_STATUS = {
    "too_large": 413,          # the body, or the picture's pixel count
    "too_many": 400,           # the character already has MAX_UPLOADS
    "not_an_image": 415,       # nothing Pillow can decode
    "unsupported_format": 415,  # an image, but not PNG/JPEG/WebP/GIF
    "bad_name": 400,           # not a <32 hex>.png name
    "not_found": 404,          # no such upload
    "storage": 409,            # the uploads folder, or the file in it, is not real (a symlink...)
    "config": 409,             # VISION_CHAIN, the budget or the ledger cannot be used
    "unavailable": 503,        # Pillow is not installed
    "vision_unavailable": 503,  # no link of VISION_CHAIN could run
    "could_not_describe": 502,  # the vision replies failed validation twice
}


class UploadError(Exception):
    """An upload refused, or one that could not be described.

    ``str()`` is a short sentence for the user; ``code`` is one of
    :data:`HTTP_STATUS`, ``http_status`` its status; ``reasons`` lists what
    each link or check said, when there is more than the sentence.
    """

    def __init__(self, message, *, code, reasons=()):
        super().__init__(message)
        self.code = code
        self.reasons = list(reasons)

    @property
    def http_status(self) -> int:
        return HTTP_STATUS.get(self.code, 400)


# Every read-modify-write of a character's refs.uploads in this module goes
# through this lock, so two uploads in the same process cannot both pass the
# count or lose each other's entry.
_ENTRIES_LOCK = threading.Lock()
# Held while Pillow decodes an upload: MAX_IMAGE_PIXELS is a module global,
# and one decode must not restore it under another.
_DECODE_LOCK = threading.Lock()


# ------------------------------------------------------------------ helpers

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _check(cancel) -> None:
    if cancel is not None:
        cancel.check()


def _megabytes(size) -> str:
    return f"{size / (1024 * 1024):g} MB"


def _check_name(name) -> None:
    """A well-formed upload name, checked before any path is built from it."""
    if not isinstance(name, str) or UPLOAD_NAME.fullmatch(name) is None:
        raise UploadError(f"{name!r} is not the name of a design reference.", code="bad_name")


def _entry(character, name):
    for entry in character["refs"]["uploads"]:
        if entry["name"] == name:
            return entry
    return None


def _unlink_quietly(path) -> None:
    if not path:
        return
    try:
        os.unlink(path)
    except OSError:
        pass


def _uploads_folder(stories, story_id, char_id, *, create) -> str:
    try:
        return stories.uploads_dir(story_id, KIND, char_id, create=create)
    except KeyError:
        raise UploadError(
            "The character's refs/uploads folder is not a real folder inside it (a symlink is never "
            "followed); remove it and upload again.", code="storage") from None


# ------------------------------------------------------------------ reading

def _stream_to(source, fh, max_bytes) -> int:
    """Copy *source* into *fh* in chunks; ``UploadError(too_large)`` as soon
    as it passes *max_bytes*. Never asks for more than one byte past the cap."""
    total = 0
    while True:
        want = min(CHUNK_BYTES, max_bytes + 1 - total)
        chunk = source.read(want)
        if not chunk:
            return total
        if not isinstance(chunk, (bytes, bytearray, memoryview)):
            raise TypeError(f"an upload is read as bytes, not {type(chunk).__name__}")
        total += len(chunk)
        if total > max_bytes:
            raise UploadError(f"The image is larger than {_megabytes(max_bytes)}.", code="too_large")
        fh.write(chunk)


def _receive(source, folder, max_bytes) -> str:
    """The body of *source* (a binary file object, or a path) in a hidden temp
    file inside *folder*; the temp file never outlives a failure."""
    handle, tmp = tempfile.mkstemp(dir=folder, prefix=_TEMP_PREFIX, suffix=".part")
    try:
        with os.fdopen(handle, "wb") as fh:
            if isinstance(source, (str, os.PathLike)):
                with open(source, "rb") as src:
                    size = _stream_to(src, fh, max_bytes)
            else:
                size = _stream_to(source, fh, max_bytes)
        if size == 0:
            raise UploadError("The file is empty, not an image.", code="not_an_image")
    except BaseException:
        _unlink_quietly(tmp)
        raise
    return tmp


# ----------------------------------------------------------------- decoding

def _pil():
    try:
        from PIL import Image, ImageOps, UnidentifiedImageError
    except ImportError:
        raise UploadError("Image uploads are not available: Pillow is not installed on this server.",
                          code="unavailable") from None
    return Image, ImageOps, UnidentifiedImageError


def _other_image_format(path):
    """The name of a common image format Pillow will not be asked to decode
    here, sniffed from the file's first bytes (no decoder runs), or None."""
    try:
        with open(path, "rb") as fh:
            head = fh.read(16)
    except OSError:
        return None
    if head[:2] == b"BM" and head[6:10] == b"\x00\x00\x00\x00":
        return "BMP"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "TIFF"
    if head[:4] == b"8BPS":
        return "PSD"
    if head[:4] == b"\x00\x00\x01\x00":
        return "ICO"
    if head[:4] == b"qoif":
        return "QOI"
    if head[:4] == b"\xff\x4f\xff\x51" or head[:12] == b"\x00\x00\x00\x0cjP  \r\n\x87\n":
        return "JPEG 2000"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"avif", b"avis"):
            return "AVIF"
        if brand in (b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1"):
            return "HEIC"
    return None


def _too_many_pixels(width=None, height=None) -> UploadError:
    size = f" ({width}x{height})" if width and height else ""
    return UploadError(f"The image has too many pixels{size}; at most {MAX_PIXELS // 1_000_000} megapixels.",
                       code="too_large")


def _not_an_image(path) -> UploadError:
    other = _other_image_format(path)
    if other:
        return UploadError(f"{other} images are not accepted; use PNG, JPEG, WebP or GIF.",
                           code="unsupported_format")
    return UploadError("The file is not an image (PNG, JPEG, WebP or GIF), or it is damaged.",
                       code="not_an_image")


def _target_mode(image) -> str:
    if image.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in image.info:
        return "RGBA"
    return "RGB"


def _decode_and_save(Image, ImageOps, UnidentifiedImageError, src, dest) -> None:
    bomb = (Image.DecompressionBombError, Image.DecompressionBombWarning)

    # 1. Identify with the allowed decoders only, and verify the file's structure.
    try:
        with Image.open(src, formats=ALLOWED_FORMATS) as probe:
            probe.verify()
    except bomb:
        raise _too_many_pixels() from None
    except UnidentifiedImageError:
        raise _not_an_image(src) from None
    except Exception:  # noqa: BLE001 - any failure to parse untrusted bytes is a refusal
        raise _not_an_image(src) from None

    # 2. verify() leaves the image unusable: reopen, check the size, decode.
    try:
        with Image.open(src, formats=ALLOWED_FORMATS) as image:
            if image.format not in _DECODED_FORMATS:
                raise UploadError(f"{image.format} images are not accepted; use PNG, JPEG, WebP or GIF.",
                                  code="unsupported_format")
            width, height = image.size
            if width < 1 or height < 1:
                raise _not_an_image(src)
            if width * height > MAX_PIXELS:
                raise _too_many_pixels(width, height)
            image.load()  # a GIF, animated WebP or APNG is on its first frame
            try:
                upright = ImageOps.exif_transpose(image)
            except Exception:  # noqa: BLE001 - a broken EXIF block is dropped, not trusted
                upright = image.copy()
    except UploadError:
        raise
    except bomb:
        raise _too_many_pixels() from None
    except Exception:  # noqa: BLE001 - truncated or corrupt data, whatever Pillow raised
        raise _not_an_image(src) from None

    # 3. RGB or RGBA, at most MAX_SIDE on the long side, a PNG with no metadata.
    try:
        mode = upright.mode
        try:
            clean = upright.convert(_target_mode(upright))  # always a new image
        except (ValueError, OSError):
            raise UploadError(f"This image's colour format ({mode}) is not supported; save it as an "
                              "8-bit PNG or a JPEG.", code="unsupported_format") from None
    finally:
        upright.close()
    try:
        if max(clean.size) > MAX_SIDE:
            clean.thumbnail((MAX_SIDE, MAX_SIDE), Image.Resampling.LANCZOS)
        # Nothing of the upload's own metadata reaches the file: the PNG writer
        # reads icc_profile, transparency and exif from info, text only from
        # the arguments of save(), which are none but the format.
        clean.info = {}
        clean.save(dest, format="PNG")
    finally:
        clean.close()


def _reencode(src, dest) -> None:
    """Decode *src* (untrusted) and write it to *dest* as a clean PNG, or
    raise :class:`UploadError`. Pillow's ``MAX_IMAGE_PIXELS`` is held at
    ``MAX_PIXELS`` (never loosened) and its bomb warning raised as an error
    for the duration, under a lock; both are restored after."""
    Image, ImageOps, UnidentifiedImageError = _pil()
    with _DECODE_LOCK, warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        saved = Image.MAX_IMAGE_PIXELS
        Image.MAX_IMAGE_PIXELS = MAX_PIXELS if saved is None else min(saved, MAX_PIXELS)
        try:
            _decode_and_save(Image, ImageOps, UnidentifiedImageError, src, dest)
        finally:
            Image.MAX_IMAGE_PIXELS = saved


# ------------------------------------------------------------------- accept

def accept_upload(stories, story_id, char_id, source, *, now, max_bytes=MAX_UPLOAD_BYTES) -> dict:
    """Accept one design reference for the character; returns its new entry
    ``{"name", "description": None, "uploaded_at": now}``.

    *source* is a binary file object (``UploadFile.file``) or a path; it is
    read only after the count check. KeyError for an unknown story or
    character; :class:`UploadError` for a refusal (``too_many``,
    ``too_large``, ``not_an_image``, ``unsupported_format``, ``storage``,
    ``unavailable``). A refusal leaves nothing behind in ``refs/uploads/``.
    """
    if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or max_bytes < 1:
        raise ValueError(f"max_bytes must be a positive number of bytes, not {max_bytes!r}")

    character = stories.read_entity(story_id, KIND, char_id)
    if len(character["refs"]["uploads"]) >= MAX_UPLOADS:
        raise UploadError(f"{character['name']} already has {MAX_UPLOADS} design references; "
                          "remove one first.", code="too_many")

    folder = _uploads_folder(stories, story_id, char_id, create=True)
    received = cleaned = stored = None
    try:
        received = _receive(source, folder, max_bytes)
        handle, cleaned = tempfile.mkstemp(dir=folder, prefix=_TEMP_PREFIX, suffix=".png")
        os.close(handle)
        _reencode(received, cleaned)

        for _ in range(_NAME_ATTEMPTS):
            name = f"{uuid.uuid4().hex}.png"
            if not os.path.lexists(os.path.join(folder, name)):
                break
        else:
            raise RuntimeError(f"no free upload name after {_NAME_ATTEMPTS} attempts")
        try:
            stored = stories.write_media(story_id, KIND, char_id, name, cleaned)
        except (KeyError, ValueError) as exc:
            raise UploadError(f"The upload could not be stored ({exc}).", code="storage") from None

        entry = {"name": name, "description": None, "uploaded_at": now}
        with _ENTRIES_LOCK:
            current = stories.read_entity(story_id, KIND, char_id)
            if len(current["refs"]["uploads"]) >= MAX_UPLOADS:
                raise UploadError(f"{current['name']} already has {MAX_UPLOADS} design references; "
                                  "remove one first.", code="too_many")
            current["refs"]["uploads"].append(entry)
            stories.write_entity(story_id, KIND, current, now=now)
        stored = None  # listed: it is the character's now
        return copy.deepcopy(entry)
    finally:
        _unlink_quietly(received)
        _unlink_quietly(cleaned)
        _unlink_quietly(stored)


# ------------------------------------------------------------------- delete

def _existing_file(stories, story_id, char_id, name):
    """The upload's file as a real path, None when there is none; UploadError
    when something in its place (or in the way) is not a real file or folder."""
    parent = stories.entity_dir(story_id, KIND, char_id)
    for part in store_mod.MEDIA_DIRS["uploads"]:
        step = os.path.join(parent, part)
        if not os.path.lexists(step):
            return None
        if os.path.islink(step) or not os.path.isdir(step):
            raise UploadError(f"The character's {part}/ folder is not a real folder (a symlink is never "
                              "followed); nothing was removed.", code="storage")
        parent = step
    folder = _uploads_folder(stories, story_id, char_id, create=False)
    path = os.path.join(folder, name)
    if not os.path.lexists(path):
        return None
    if os.path.islink(path) or not os.path.isfile(path):
        raise UploadError(f"{name} is not a regular file (a symlink is never followed); nothing was removed.",
                          code="storage")
    return path


def delete_upload(stories, story_id, char_id, name, *, now) -> dict:
    """Remove one design reference: its entry, then its file.

    *name* must be a ``<32 hex>.png`` name (checked before any path is
    built). Only a regular file directly inside the character's real
    ``refs/uploads/`` is removed; a symlink in its place is refused and kept,
    and so is its entry. Returns ``{"name", "entry_removed", "file_removed"}``.
    KeyError for an unknown story or character; ``UploadError(not_found)``
    when the character has neither the entry nor the file.
    """
    _check_name(name)
    with _ENTRIES_LOCK:
        character = stories.read_entity(story_id, KIND, char_id)
        path = _existing_file(stories, story_id, char_id, name)
        listed = _entry(character, name) is not None
        if not listed and path is None:
            raise UploadError(f"{character['name']} has no design reference {name}.", code="not_found")
        if listed:
            character["refs"]["uploads"] = [e for e in character["refs"]["uploads"] if e["name"] != name]
            stories.write_entity(story_id, KIND, character, now=now)
    file_removed = False
    if path is not None:
        try:
            os.unlink(path)
            file_removed = True
        except FileNotFoundError:
            pass
    return {"name": name, "entry_removed": listed, "file_removed": file_removed}


# ------------------------------------------------------------------ describe

def vision_prompt(language) -> str:
    """U1 as one text for a vision adapter: they send ``request.prompt`` as
    the user turn next to the image and have no system turn or schema slot,
    so the system text, the ask and the reply's schema are joined."""
    system, user, schema = prompts.build_u1(language=language)
    shape = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    return f"{system}\n\n{user}\n\nThe reply's JSON schema: {shape}"


def _open_ledger(stories, story_id) -> ledger_mod.CostLedger:
    """The story's ledger, checked before anything is spent (the pattern of
    ``steps.style_preview._open_ledger``, as ``voices.py`` repeats it)."""
    path = os.path.join(stories.story_dir(story_id), LEDGER_NAME)
    if os.path.islink(path):
        raise UploadError(f"{LEDGER_NAME} is a symlink, which is never followed, so this story's "
                          "spending cannot be checked; replace it with the file itself.", code="config")
    if os.path.lexists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if (not isinstance(data, dict) or data.get("$schema") != ledger_mod.SCHEMA
                    or not isinstance(data.get("entries"), list)):
                raise ValueError(f"not a {ledger_mod.SCHEMA} document")
            sum(float(entry["est_usd"]) for entry in data["entries"])
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise UploadError(f"{LEDGER_NAME} cannot be read ({type(exc).__name__}: {exc}), so this "
                              "story's spending cannot be checked; fix it before describing an upload.",
                              code="config") from None
    return ledger_mod.CostLedger(path)


def _vision_tokens(request) -> int:
    """``vision._CompatVision.estimate``'s token count, for a free link whose
    adapter estimates nothing."""
    from clipping.providers import vision

    return len(request.prompt or "") // 4 + vision.TOKENS_PER_IMAGE * len(request.images or ())


def _units(answered, request, adapters):
    """``(unit, qty)`` for the ledger: the adapter's own estimate when it has
    one (a paid link), else the same token count it would have used."""
    adapter = gen.adapter_for(gen.VISION, answered.provider, adapters)
    try:
        estimate = adapter.estimate(answered, request) if adapter is not None else None
    except Exception:  # noqa: BLE001 - a missing price must not lose the booking
        estimate = None
    unit, qty = getattr(estimate, "unit", None), getattr(estimate, "qty", None)
    if unit in ledger_mod.UNITS and isinstance(qty, (int, float)):
        return unit, qty
    return "token", _vision_tokens(request)


def _book(ledger, result, answered, request, adapters) -> float:
    """Book one answered call; a paid one is also added to today's spend.
    ``run_generation_chain`` books nothing itself: this is the one booking."""
    paid = bool(result.paid)
    est = float(result.est_cost) if paid else 0.0
    unit, qty = _units(answered, request, adapters)
    ledger.append(step=STEP, provider=answered.provider, model=gating.api_model_id(gen.VISION, answered),
                  unit=unit, qty=qty, est_usd=est, paid=paid)
    if paid and result.est_cost > 0:
        budget_mod.record(result.est_cost)
    return round(est, 4)


def _reply_errors(result):
    """``(notes, errors)`` from one answer: the reply text is
    ``GenResult.meta["text"]`` (``providers/vision.py``, every adapter)."""
    text = (result.meta or {}).get("text") or ""
    try:
        value = jsonx.extract_json(text)
    except ValueError as exc:
        return None, [str(exc)]
    errors = schemas.u1_errors(value)
    if errors:
        return None, errors
    notes = " ".join(value["appearance_notes"].split())
    if len(notes) > DESCRIPTION_MAX_CHARS:
        return None, [f"$.appearance_notes: {len(notes)} characters, expected at most {DESCRIPTION_MAX_CHARS}"]
    return notes, []


def describe_upload(stories, story_id, char_id, name, *, env, on_log, cancel, adapters=None, transport=None) -> str:
    """Describe one design reference through ``VISION_CHAIN`` (prompt U1)
    and store the notes as its ``description``; returns them.

    *env* is the Settings values (merged over the process environment here).
    The story's route, ``allow_paid`` and the budget caps (with the story's
    ledger total) and the free-tier limiter gate every link, as for the
    style preview. Each answered call is booked (``step="upload_description"``);
    a reply that fails U1's validation is asked for once more with the same
    request. KeyError for an unknown story or character; UploadError:
    ``bad_name``, ``not_found``, ``config``, ``vision_unavailable`` (every
    link's reason in ``reasons``), ``could_not_describe`` (the validation
    errors in ``reasons``; nothing is stored).
    """
    _check_name(name)
    _check(cancel)
    story = stories.get(story_id)
    character = stories.read_entity(story_id, KIND, char_id)
    who = character["name"]
    if _entry(character, name) is None:
        raise UploadError(f"{who} has no design reference {name}.", code="not_found")
    try:
        path = stories.media_path(story_id, KIND, char_id, name)
    except KeyError:
        raise UploadError(f"The file of {who}'s design reference {name} is missing or not a regular file; "
                          "upload it again.", code="not_found") from None

    if adapters is None:
        adapters_mod.load_all()
    merged = gating.merged_env(env)
    try:
        chain = gen.chain_from_env(gen.VISION, merged)
    except ChainError as exc:
        raise UploadError(f"{gen.ENV_NAMES[gen.VISION]} cannot be used: {exc}", code="config") from None
    try:
        budget_obj = gating.budget_of(merged)
    except ValueError as exc:
        raise UploadError(f"The budget settings cannot be used: {exc}", code="config") from None

    ledger = _open_ledger(stories, story_id)
    check = gating.budget_check(budget_obj, story_spent=lambda: ledger.totals()["est_usd"])
    limiter = gating.FreeTierLimiter()
    route = story["generation_profile"]["route"]
    request = gen.GenRequest(
        kind=gen.VISION, prompt=vision_prompt(story["language"]), images=(path,),
        extra={"max_tokens": prompts.MAX_TOKENS[PROMPT_ID], "temperature": prompts.TEMPERATURE[PROMPT_ID]},
    )
    by_label = {describe(link): link for link in chain}

    def explain(label, reason):
        link = by_label.get(label)
        if reason == "paid link; allow_paid is off" and link is not None:
            est = gating.link_summary(gen.VISION, link, merged, budget_obj, request, adapters=adapters)["est_usd"]
            return (f"{label}: {reason} (est ${est:.4f} per description; today ${budget_mod.day_spent():.2f} "
                    f"of the ${budget_obj.daily_cap_usd:.2f} daily cap)")
        return f"{label}: {reason}"

    notes, errors = None, []
    for attempt in (1, 2):
        _check(cancel)
        try:
            result, answered = gen.run_generation_chain(
                gen.VISION, chain, request, env=merged, allow_paid=budget_obj.allow_paid, route=route,
                on_log=on_log, budget_check=check, limiter=limiter, adapters=adapters,
                transport=transport, cancel=cancel,
            )
        except gen.NoRunnableLink as exc:
            reasons = [explain(label, reason) for label, reason in exc.failures] or [str(exc)]
            raise UploadError(f"No vision link could describe {who}'s design reference on route {route}.",
                              code="vision_unavailable", reasons=reasons) from None
        # Answered: booked first, whatever the reply is worth.
        _book(ledger, result, answered, request, adapters)
        notes, errors = _reply_errors(result)
        if notes is not None:
            break
        if attempt == 1:
            on_log(f"⚠️ {PROMPT_ID} reply rejected ({'; '.join(errors[:2])}); asking once more")
    if notes is None:
        raise UploadError(f"The design reference of {who} could not be described: the vision replies failed "
                          "validation twice.", code="could_not_describe", reasons=errors)

    with _ENTRIES_LOCK:
        current = stories.read_entity(story_id, KIND, char_id)
        entry = _entry(current, name)
        if entry is None:
            raise UploadError(f"{who}'s design reference {name} was removed while it was being described.",
                              code="not_found")
        entry["description"] = notes
        stories.write_entity(story_id, KIND, current, now=_utc_now())
    on_log(f"👁 {who}: design reference described via {describe(answered)}")
    return notes


def upload_notes(character):
    """The descriptions of the character's design references, joined for K1
    (``prompts.build_k1(upload_notes=...)``); None when there is none yet."""
    refs = (character or {}).get("refs") or {}
    notes = []
    for entry in refs.get("uploads") or ():
        text = " ".join(str(entry.get("description") or "").split()).rstrip(" .;")
        if text:
            notes.append(text)
    return "; ".join(notes) if notes else None
