"""A still's pixel size, read from its file header (AI Story phase 6, stage
13b): the render plan needs it to centre-crop a still that is not 9:16
(``filtergraph.shot_argv``'s *image_size*), and nothing else here decodes a
pixel.

:func:`image_size` returns ``(width, height)`` or None. It reads PNG (the
``IHDR`` chunk), JPEG (the first start-of-frame marker, walking past the APPn
and other segments before it) and WebP (``VP8``, ``VP8L`` and ``VP8X``).
Anything else -- another format, a truncated or damaged header, a size of
zero, a file that cannot be opened -- is None: the caller then renders the
still as it always did, never fails on it.

Stdlib only: no PIL, which the pytest-only CI environment does not install
(DEC-012, A-096).
"""

from __future__ import annotations

import os
import struct

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# JPEG start-of-frame markers: C0..CF without DHT (C4), JPG (C8) and DAC (CC).
_JPEG_SOF = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
# Markers that carry no length: TEM, RST0..7, SOI.
_JPEG_BARE = frozenset([0x01, 0xD8, *range(0xD0, 0xD8)])
_JPEG_MAX_SEGMENTS = 1024


def _sized(width: int, height: int):
    return (width, height) if width > 0 and height > 0 else None


def _png(head: bytes):
    if len(head) < 24 or head[12:16] != b"IHDR":
        return None
    return _sized(*struct.unpack(">II", head[16:24]))


def _webp(head: bytes):
    if len(head) < 30:
        return None
    fourcc, data = head[12:16], head[20:30]
    if fourcc == b"VP8 ":
        if data[3:6] != b"\x9d\x01\x2a":
            return None
        width, height = struct.unpack("<HH", data[6:10])
        return _sized(width & 0x3FFF, height & 0x3FFF)
    if fourcc == b"VP8L":
        if data[0] != 0x2F:
            return None
        b0, b1, b2, b3 = data[1:5]
        return _sized(1 + (b0 | (b1 & 0x3F) << 8), 1 + (b1 >> 6 | b2 << 2 | (b3 & 0x0F) << 10))
    if fourcc == b"VP8X":
        width = 1 + int.from_bytes(data[4:7], "little")
        height = 1 + int.from_bytes(data[7:10], "little")
        return _sized(width, height)
    return None


def _jpeg(handle):
    """The size in the first start-of-frame segment; *handle* is just past
    the SOI marker."""
    for _ in range(_JPEG_MAX_SEGMENTS):
        byte = handle.read(1)
        if byte != b"\xff":
            return None
        marker = 0xFF
        while marker == 0xFF:  # fill bytes before the marker
            byte = handle.read(1)
            if not byte:
                return None
            marker = byte[0]
        if marker in _JPEG_BARE:
            continue
        if marker in (0xD9, 0xDA):  # EOI, or the scan began: no frame header before it
            return None
        raw = handle.read(2)
        if len(raw) < 2:
            return None
        length = struct.unpack(">H", raw)[0]
        if length < 2:
            return None
        if marker in _JPEG_SOF:
            frame = handle.read(5)
            if len(frame) < 5:
                return None
            height, width = struct.unpack(">HH", frame[1:5])
            return _sized(width, height)
        handle.seek(length - 2, os.SEEK_CUR)
    return None


def image_size(path):
    """``(width, height)`` of the PNG, JPEG or WebP at *path*, from its
    header alone; None when it is none of those or its header cannot be
    read (module docstring)."""
    try:
        with open(path, "rb") as handle:
            head = handle.read(32)
            if head.startswith(_PNG_SIGNATURE):
                return _png(head)
            if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
                return _webp(head)
            if head[:2] == b"\xff\xd8":
                handle.seek(2)
                return _jpeg(handle)
    except (OSError, ValueError, TypeError):
        return None
    return None
