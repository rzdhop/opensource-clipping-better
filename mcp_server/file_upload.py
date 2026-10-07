"""``file_upload``: a file from the chat onto this server, under ``outputs/``
only (plan 33 stage 2).

The remote writer has no shell here; a reference voice, a keyframe it drew
elsewhere or a line list reaches the server through this tool, base64 in one
message. The guard mirrors ``comfy_download``'s containment, stricter: the
outputs dir is the only root (the unit's ``ReadWritePaths`` allows nothing
else), the extension must be on the allowlist, the decoded size is capped,
and an existing file is never replaced unless asked. Stdlib only.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import tempfile

# What the chat may send: sound, pictures, clips, and the two text shapes a
# line list or a note takes. Anything executable or ambiguous is refused.
ALLOWED_EXTENSIONS = (".wav", ".mp3", ".flac", ".png", ".jpg", ".jpeg", ".mp4", ".json", ".txt")
KINDS = {"audio": (".wav", ".mp3", ".flac"), "image": (".png", ".jpg", ".jpeg"), "video": (".mp4",),
         "json": (".json",), "text": (".txt",)}
MAX_MIB = 25


class UploadError(Exception):
    """The upload was refused; the message says why."""


def upload_path(dest_path: str, outputs_dir: str) -> str:
    """The absolute path *dest_path* names under *outputs_dir*, or
    ``UploadError``: a relative path (``faille_damour/ep01/voices/ref.wav``)
    or an absolute one already inside the outputs dir; ``..``, symlinks and
    siblings of the outputs dir (``outputs_evil``) are refused by resolving
    the real path and demanding ``root + os.sep`` as its prefix."""
    if not dest_path or not str(dest_path).strip():
        raise UploadError("dest_path is required: a path under the outputs dir")
    root = os.path.realpath(outputs_dir)
    candidate = dest_path if os.path.isabs(dest_path) else os.path.join(outputs_dir, dest_path)
    parent = os.path.realpath(os.path.dirname(candidate))
    real = os.path.join(parent, os.path.basename(candidate))
    if not (parent == root or parent.startswith(root + os.sep)):
        raise UploadError(f"{dest_path!r} resolves outside the outputs dir; uploads land under outputs/ only")
    if os.path.islink(real) or os.path.isdir(real):
        raise UploadError(f"{dest_path!r} is a directory or a link; name a file")
    base = os.path.basename(real)
    if base.startswith("."):
        raise UploadError("a dotfile is refused (the tools hide them)")
    return real


def check_extension(real: str, kind: str | None) -> str:
    """The lower-case extension of *real*, on the allowlist and, when *kind*
    is given, one of that kind's; the kind otherwise."""
    ext = os.path.splitext(real)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError(f"{ext or 'no extension'!s} is not allowed; one of {', '.join(ALLOWED_EXTENSIONS)}")
    if kind:
        if kind not in KINDS:
            raise UploadError(f"unknown kind {kind!r}; one of {', '.join(KINDS)}")
        if ext not in KINDS[kind]:
            raise UploadError(f"a {kind} upload takes {', '.join(KINDS[kind])}, not {ext}")
        return kind
    return next(k for k, exts in KINDS.items() if ext in exts)


def decode(content_base64: str, *, max_mib: int = MAX_MIB) -> bytes:
    """The bytes of *content_base64* (a bare base64 string, or a data URL
    whose payload is taken), at most *max_mib*."""
    text = (content_base64 or "").strip()
    if text.startswith("data:") and "," in text:
        text = text.split(",", 1)[1]
    if not text:
        raise UploadError("content_base64 is empty")
    # The cap is checked on the encoded length first so a 100 MiB string is
    # never decoded into memory: base64 inflates by 4/3.
    limit = max_mib * 1024 * 1024
    if len(text) > limit * 4 // 3 + 4:
        raise UploadError(f"the upload is over the {max_mib} MiB limit; split it or scp it")
    try:
        data = base64.b64decode(text, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise UploadError(f"content_base64 is not valid base64: {exc}") from exc
    if not data:
        raise UploadError("the decoded file is empty")
    if len(data) > limit:
        raise UploadError(f"the file is {len(data) / (1024 * 1024):.1f} MiB, over the {max_mib} MiB limit")
    return data


def write_upload(real: str, data: bytes, *, overwrite: bool = False) -> None:
    """*data* at *real*, written whole to a temp file beside it then
    ``os.replace``d (a reader sees nothing or the whole file)."""
    if os.path.exists(real) and not overwrite:
        raise UploadError(f"{real} exists; send overwrite=true to replace it")
    os.makedirs(os.path.dirname(real), exist_ok=True)
    handle, tmp = tempfile.mkstemp(dir=os.path.dirname(real), prefix=f".{os.path.basename(real)}-", suffix=".tmp")
    try:
        with os.fdopen(handle, "wb") as fh:
            fh.write(data)
        os.replace(tmp, real)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def save_upload(dest_path: str, content_base64: str, *, outputs_dir: str, kind: str | None = None,
                overwrite: bool = False, probe=None) -> dict:
    """The whole tool: guard, decode, write; the answer names the file, its
    size, its sha256 and its kind (*probe(path)*, when given, adds what it
    returns for a sound file: its duration)."""
    real = upload_path(dest_path, outputs_dir)
    kind = check_extension(real, kind)
    data = decode(content_base64)
    write_upload(real, data, overwrite=overwrite)
    answer = {"path": real, "relative": os.path.relpath(real, os.path.realpath(outputs_dir)), "kind": kind,
              "size_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest(), "overwritten": overwrite}
    if probe and kind == "audio":
        try:
            answer.update(probe(real))
        except Exception as exc:  # the file is saved either way; the probe is a courtesy
            answer["probe_error"] = str(exc)
    return answer


def register_file_upload(mcp, *, outputs_dir: str, probe=None) -> None:
    """Register ``file_upload`` on a FastMCP instance."""
    from fastmcp.exceptions import ToolError

    @mcp.tool()
    def file_upload(dest_path: str, content_base64: str, kind: str | None = None, overwrite: bool = False) -> dict:
        """Free. Send a file to this server, base64-encoded, saved under the outputs dir only (dest_path
        relative to it, e.g. 'faille_damour/ep01/voices/ref_rida.wav'; '..', links and anything outside are
        refused). Extensions: wav, mp3, flac, png, jpg, jpeg, mp4, json, txt; kind (audio/image/video/json/text)
        is checked against the extension when given. At most 25 MiB decoded; an existing file needs
        overwrite=true. Answers the saved path, size, sha256 and, for a sound file, its duration -- a voice
        reference for tts_line(provider='chatterbox') or voice_ref_make, a keyframe, a line list for tts_batch."""
        try:
            return save_upload(dest_path, content_base64, outputs_dir=outputs_dir, kind=kind, overwrite=overwrite,
                               probe=probe)
        except UploadError as exc:
            raise ToolError(str(exc)) from exc
