"""Small cached thumbnails of a story entity's images (dashboard overhaul
stage 5, DEC-257).

The dashboard draws a speaker avatar at 28 px, an entity tile at ~200 px and
a stories-list cover at ~340 px, and each used to fetch the full portrait
(550-640 kB). ``GET /api/stories/{id}/media/{kind}/{eid}/{name}?size=thumb``
answers :func:`thumbnail`'s file instead: a JPEG :data:`THUMB_WIDTH` px wide
(never enlarged), made on the first request and kept next to the original as
``<name>.thumb.jpg`` -- a name no ``StoryStore.MEDIA_NAME_PATTERNS`` pattern
matches, so the kept file is reachable only through the parameter.

The rules are the media route's own:

- the original is a real path the store already checked
  (``StoryStore.media_path``: a regular file in the entity's real folder, no
  symlink at any level);
- the thumbnail's place must hold nothing or a regular file: a symlink or a
  directory there is :class:`ThumbRefused`, never followed, read or replaced;
- a kept thumbnail is current while its modification time equals the
  original's (it is stamped with it): a regenerated image, which reuses its
  name, gets a new one on its next request;
- the write is atomic (a temp file in the same folder, then ``os.replace``);
  a failure leaves no temp file behind.

Pillow is imported lazily: it is a declared dependency of the image (Clips
mode needs it), not of the stdlib-only test suite (DEC-012). Without it, or
for a file it cannot read, :class:`ThumbUnavailable` tells the route to serve
the original -- a thumbnail is an optimisation, never a broken avatar.
"""

from __future__ import annotations

import os
import tempfile

THUMB_WIDTH = 480  # sharp on a 340 px list cover at 2x; a tenth of the full portrait (DEC-257, the orchestrator's re-pin)
# A 9:16 portrait at 160 px wide is 284 px tall; this only bounds a freak
# panorama-in-portrait so the thumbnail stays small.
THUMB_MAX_HEIGHT = THUMB_WIDTH * 4
THUMB_SUFFIX = ".thumb.jpg"
THUMB_QUALITY = 82
SIZES = ("thumb",)
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp")
_TEMP_PREFIX = ".thumb-"


class ThumbUnavailable(Exception):
    """No thumbnail could be made (Pillow missing, an unreadable image, a
    write that failed): the caller serves the original."""


class ThumbRefused(Exception):
    """The thumbnail's place holds a symlink or anything but a regular file;
    it is never followed nor replaced."""


def thumb_name(name: str) -> str:
    """``portrait.png`` -> ``portrait.png.thumb.jpg``."""
    return f"{name}{THUMB_SUFFIX}"


def is_image_name(name) -> bool:
    """Whether *name* is an image a thumbnail can be made of (by extension)."""
    return isinstance(name, str) and os.path.splitext(name)[1].lower() in IMAGE_EXTENSIONS


def thumbnail(original: str) -> str:
    """The path of *original*'s thumbnail, made (or made again) when it is
    missing or older than the original.

    *original* must be a real path already checked by the store. Raises
    :class:`ThumbRefused` for a symlink or a non-file in the thumbnail's
    place, :class:`ThumbUnavailable` when none can be made.
    """
    folder = os.path.dirname(original)
    path = os.path.join(folder, thumb_name(os.path.basename(original)))
    try:
        source = os.stat(original)
    except OSError as exc:
        raise ThumbUnavailable(str(exc)) from None
    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isfile(path):
            raise ThumbRefused(f"{os.path.basename(path)} is not a regular file; it is never followed")
        try:
            if os.stat(path, follow_symlinks=False).st_mtime_ns == source.st_mtime_ns:
                return path
        except OSError:
            pass
    _write(original, path, source.st_mtime_ns)
    return path


def _write(original: str, path: str, mtime_ns: int) -> None:
    try:
        from PIL import Image, ImageOps
    except ImportError:
        raise ThumbUnavailable("Pillow is not installed") from None

    folder = os.path.dirname(path)
    handle, temp = tempfile.mkstemp(prefix=_TEMP_PREFIX, suffix=".jpg", dir=folder)
    try:
        with os.fdopen(handle, "wb") as out:
            with Image.open(original) as image:
                image = ImageOps.exif_transpose(image)
                image.thumbnail((THUMB_WIDTH, THUMB_MAX_HEIGHT), Image.LANCZOS)
                if image.mode in ("RGBA", "LA") or (image.mode == "P" and "transparency" in image.info):
                    rgba = image.convert("RGBA")
                    flat = Image.new("RGB", rgba.size, (0, 0, 0))
                    flat.paste(rgba, mask=rgba.split()[-1])
                    image = flat
                elif image.mode != "RGB":
                    image = image.convert("RGB")
                image.save(out, format="JPEG", quality=THUMB_QUALITY, optimize=True, progressive=True)
        os.utime(temp, ns=(mtime_ns, mtime_ns))
        # Checked again just before the swap: a symlink planted meanwhile is
        # refused, not replaced (os.replace would swap the link itself, never
        # its target, but the rule is "never replaced").
        if os.path.islink(path) or (os.path.lexists(path) and not os.path.isfile(path)):
            raise ThumbRefused(f"{os.path.basename(path)} is not a regular file; it is never followed")
        os.replace(temp, path)
    except ThumbRefused:
        _discard(temp)
        raise
    except Exception as exc:  # Pillow raises a dozen kinds for a bad file
        _discard(temp)
        raise ThumbUnavailable(f"{type(exc).__name__}: {exc}") from None


def _discard(temp: str) -> None:
    try:
        os.unlink(temp)
    except OSError:
        pass
