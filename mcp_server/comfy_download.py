"""comfy_download -- hand a rendered file back through MCP as bytes.

The other file tools of this server only return previews: ``view_file`` and
``comfy_fetch`` give a thumbnail for an image and a contact sheet for a clip,
never the file itself. This tool returns the actual bytes, base64-encoded in an
MCP embedded resource, so the client can save the file locally and forward it
to the user (a chat attachment, a download card).

Drop this module next to the server and register it once::

    from mcp_server.comfy_download import register_comfy_download
    register_comfy_download(mcp, outputs_dir=OUTPUTS_DIR, repo_root=REPO_ROOT)

where ``mcp`` is the server's ``FastMCP`` (fastmcp 2+ / mcp 1.x) or
``MCPServer`` (mcp 2.x) instance and the two paths are the same ones
``view_file`` resolves against.
"""

from __future__ import annotations

import base64
import mimetypes
import os

from mcp.types import BlobResourceContents, EmbeddedResource

# This server is built on the standalone ``fastmcp`` package; the official SDK
# ships the same decorator-style server as FastMCP (mcp 1.x) or MCPServer
# (mcp 2.x). All three offer the ``@mcp.tool()`` decorator, which is all we use.
try:  # the standalone fastmcp package (this server)
    from fastmcp import FastMCP as _Server
    from fastmcp.exceptions import ToolError
except ModuleNotFoundError:
    try:  # mcp >= 2
        from mcp.server.mcpserver import MCPServer as _Server
        from mcp.server.mcpserver.exceptions import ToolError
    except ModuleNotFoundError:  # mcp 1.x
        from mcp.server.fastmcp import FastMCP as _Server
        from mcp.server.fastmcp.exceptions import ToolError

# Default and hard ceiling, in MiB. Base64 inflates the payload by a third and
# the MCP transport carries it as one JSON message, so a 50 MiB clip is already
# ~67 MiB of text on the wire. Anything bigger should go through scp.
DEFAULT_MAX_MIB = 25
CEILING_MAX_MIB = 50

# Extensions mimetypes may not know on a bare server.
_EXTRA_MIME = {
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".mov": "video/quicktime",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".srt": "text/plain",
    ".json": "application/json",
}


def resolve_server_path(path: str, outputs_dir: str, repo_root: str) -> str:
    """Resolve a path the way the other tools do and refuse anything outside.

    Accepted forms: absolute, relative to the outputs dir, relative to the repo.
    The resolved file must be a regular file inside the outputs dir or the repo
    (symlinks are followed before the containment check, so a link pointing out
    of both roots is refused too).
    """
    roots = [os.path.realpath(outputs_dir), os.path.realpath(repo_root)]

    if os.path.isabs(path):
        candidates = [path]
    else:
        candidates = [os.path.join(outputs_dir, path), os.path.join(repo_root, path)]

    for candidate in candidates:
        real = os.path.realpath(candidate)
        if not os.path.isfile(real):
            continue
        if any(real == root or real.startswith(root + os.sep) for root in roots):
            return real
        raise ValueError(f"{path!r} resolves outside the outputs dir and the repo")

    raise FileNotFoundError(f"no such file: {path!r}")


def guess_mime(path: str) -> str:
    """A media type for the client; octet-stream when nothing better is known."""
    ext = os.path.splitext(path)[1].lower()
    if ext in _EXTRA_MIME:
        return _EXTRA_MIME[ext]
    guessed, _ = mimetypes.guess_type(path)
    return guessed or "application/octet-stream"


def register_comfy_download(mcp: _Server, *, outputs_dir: str, repo_root: str) -> None:
    """Register the ``comfy_download`` tool on an existing FastMCP / MCPServer instance."""

    @mcp.tool()
    def comfy_download(path: str, max_mib: int = DEFAULT_MAX_MIB) -> EmbeddedResource:
        """Free. The file itself (a clip, an image, an audio file), base64-encoded, so the
        client can save it and hand it to the user -- comfy_fetch and view_file only return
        previews. Paths as for view_file: absolute, or relative to the outputs dir or the
        repo. Refuses files over max_mib (default 25, ceiling 50): base64 inflates the
        payload by a third and it travels as one message. Bigger files: scp them."""
        # ToolError is the one exception both SDK generations relay to the client
        # with its message intact; anything else becomes a bare "error executing tool".
        try:
            real = resolve_server_path(path, outputs_dir, repo_root)
        except (FileNotFoundError, ValueError) as exc:
            raise ToolError(str(exc)) from exc

        limit_mib = max(1, min(int(max_mib), CEILING_MAX_MIB))
        size = os.path.getsize(real)
        if size > limit_mib * 1024 * 1024:
            raise ToolError(
                f"{os.path.basename(real)} is {size / (1024 * 1024):.1f} MiB, "
                f"over the {limit_mib} MiB limit; copy it with scp instead"
            )

        with open(real, "rb") as handle:
            blob = base64.b64encode(handle.read()).decode("ascii")

        return EmbeddedResource(
            type="resource",
            resource=BlobResourceContents(
                uri=f"file://{real}",
                mimeType=guess_mime(real),
                blob=blob,
            ),
        )
