"""Plan 32 stage 5: look at an episode and share it.

``episode_sheet`` -- one contact sheet of every shot of an episode, in order
(a frame of the shot's clip when there is one, else its keyframe, else a grey
tile), so the chat can judge the whole episode in a glance.
``episode_export`` -- a share copy of ``episode_final.mp4`` re-encoded to fit
under the ``comfy_download`` ceiling, so the chat can hand the video over.

Both read the story only through the store's public accessors
(``read_episode_doc``, ``episode_asset_path``, ``episode_file_path``,
``episode_dir``); the two files they write sit next to the episode's own
(``episode_sheet.png``, ``episode_share.mp4``) and never replace one of its
documents or its ``episode_final.mp4``.
"""

from __future__ import annotations

import io
import math
import os
import subprocess
import tempfile
from typing import Optional

from fastmcp.exceptions import ToolError
from fastmcp.utilities.types import Image

from . import media
from .comfy_download import CEILING_MAX_MIB, DEFAULT_MAX_MIB

SHEET_NAME = "episode_sheet.png"
SHARE_NAME = "episode_share.mp4"
FINAL_NAME = "episode_final.mp4"
# The quality steps of the share copy, best first (libx264 CRF: higher is smaller).
CRF_LADDER = (23, 26, 28, 30, 32)
DEFAULT_COLUMNS = 4
DEFAULT_SHEET_PX = 1600
MIN_TILE_W = 64
GAP = 6
CAPTION_H = 22
GREY = (70, 70, 70)
PAPER = (24, 24, 24)
ENCODE_TIMEOUT_S = 1800
MIB = 1024 * 1024


def _fail(exc: Exception) -> ToolError:
    """A store refusal as a tool error with its reason."""
    if isinstance(exc, KeyError):
        return ToolError(f"not found: {exc.args[0] if exc.args else exc}")
    return ToolError(f"{type(exc).__name__}: {exc}")


def _font(size: int):
    from PIL import ImageFont

    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # an old Pillow: the fixed bitmap font
        return ImageFont.load_default()


def _frame_of_clip(path: str) -> Optional[bytes]:
    """PNG bytes of the clip's first frame (the frame at 1 s when it lasts 2 s or more),
    or None when ffmpeg cannot read one."""
    try:
        info = media.probe_video(path)
        at = 1.0 if info["duration_s"] >= 2.0 else 0.0
        with tempfile.TemporaryDirectory() as tmp:
            out_path = os.path.join(tmp, "frame.png")
            run = subprocess.run([media._ffmpeg("ffmpeg"), "-v", "error", "-y", "-ss", f"{at:.3f}", "-i", path,
                                  "-frames:v", "1", out_path], capture_output=True, text=True, check=False)
            if run.returncode != 0 or not os.path.isfile(out_path):
                return None
            with open(out_path, "rb") as fh:
                return fh.read()
    except (media.MediaError, OSError, ValueError):
        return None


def _shot_source(stories, story_id: str, ep: int, shot: dict) -> tuple:
    """``(state, image_bytes_or_path, source)`` of one shot: its clip's frame when its
    clip is on disk, else its keyframe, else missing. *source* is the file's path inside
    the episode folder (None when missing)."""
    assets = shot.get("assets") or {}
    for kind, field in (("clips", "video"), ("shots", "image")):
        rel = assets.get(field)
        if not rel:
            continue
        try:
            path = stories.episode_asset_path(story_id, ep, kind, rel.rpartition("/")[2])
        except KeyError:
            continue
        if not os.path.isfile(path):
            continue
        if kind == "clips":
            frame = _frame_of_clip(path)
            if frame is not None:
                return "clip", frame, rel
            continue  # an unreadable clip: fall back to the keyframe
        return "keyframe", path, rel
    return "missing", None, None


def _open(source):
    from PIL import Image as PILImage

    if isinstance(source, (bytes, bytearray)):
        return PILImage.open(io.BytesIO(source)).convert("RGB")
    return PILImage.open(source).convert("RGB")


def _sheet(tiles: list, columns: int, max_px: int) -> bytes:
    """One PNG of *tiles* (``[(label, image_or_None)]``) laid out in rows of *columns*,
    sized so neither side passes *max_px*."""
    from PIL import Image as PILImage, ImageDraw

    rows = math.ceil(len(tiles) / columns)
    first = next((im for _label, im in tiles if im is not None), None)
    ratio = (first.height / first.width) if first is not None else 16 / 9
    cols_used = min(columns, len(tiles))
    width_fit = (max_px - GAP * (cols_used + 1)) // cols_used
    height_fit = int(((max_px - GAP * (rows + 1)) // rows - CAPTION_H) / ratio)
    tile_w = max(MIN_TILE_W, min(width_fit, height_fit))
    tile_h = max(1, int(round(tile_w * ratio)))
    cell_h = tile_h + CAPTION_H
    sheet = PILImage.new("RGB", (cols_used * tile_w + GAP * (cols_used + 1), rows * cell_h + GAP * (rows + 1)), PAPER)
    draw = ImageDraw.Draw(sheet)
    font = _font(14)
    for index, (label, im) in enumerate(tiles):
        row, col = divmod(index, columns)
        x = GAP + col * (tile_w + GAP)
        y = GAP + row * (cell_h + GAP)
        if im is None:
            sheet.paste(GREY, (x, y, x + tile_w, y + tile_h))
        else:
            fitted = im.copy()
            fitted.thumbnail((tile_w, tile_h))
            sheet.paste(fitted, (x + (tile_w - fitted.width) // 2, y + (tile_h - fitted.height) // 2))
        draw.text((x + 4, y + tile_h + 3), label, fill=(235, 235, 235), font=font)
    buf = io.BytesIO()
    sheet.save(buf, "PNG")
    return buf.getvalue()


def _atomic_write(path: str, data: bytes) -> None:
    tmp = f"{path}.part"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def _jpeg(png: bytes, max_px: int) -> bytes:
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(png)) as im:
        im = im.convert("RGB")
        im.thumbnail((max_px, max_px))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=85)
        return buf.getvalue()


def build_sheet(backend, story_id: str, episode: int, columns: int, max_px: int):
    """The sheet's content blocks: the picture and its mapping. Refuses in plain words."""
    stories = backend.stories
    try:
        board = stories.read_episode_doc(story_id, episode, "storyboard.json")
    except (KeyError, ValueError) as exc:
        raise _fail(exc) from exc
    if not board or not board.get("shots"):
        raise ToolError(f"episode {episode} of story {story_id} has no storyboard yet: nothing to show "
                        "(run the script and storyboard steps first)")
    columns = max(1, min(int(columns), 12))
    max_px = max(256, min(int(max_px), 4000))
    tiles, shots = [], []
    for shot in board["shots"]:
        state, source, rel = _shot_source(stories, story_id, episode, shot)
        shots.append({"id": shot["shot_id"], "state": state, "source": rel})
        tiles.append((f"{shot['shot_id']}  {state}", _open(source) if source is not None else None))
    png = _sheet(tiles, columns, max_px)
    try:
        folder = stories.episode_dir(story_id, episode)
    except KeyError as exc:
        raise _fail(exc) from exc
    path = os.path.join(folder, SHEET_NAME)
    _atomic_write(path, png)
    from PIL import Image as PILImage

    with PILImage.open(io.BytesIO(png)) as im:
        width, height = im.size
    answer = {"path": os.path.relpath(os.path.realpath(path), os.path.realpath(backend.outputs_dir)),
              "shots": shots, "width": width, "height": height,
              "note": f"{len(shots)} shots, left to right then top to bottom; each tile says its shot id and "
                      "whether it shows the clip, the keyframe, or nothing (missing)"}
    return [Image(data=_jpeg(png, max_px), format="jpeg"), answer]


def _encode(src: str, dest: str, crf: int) -> None:
    cmd = [media._ffmpeg("ffmpeg"), "-v", "error", "-y", "-i", src, "-c:v", "libx264", "-preset", "medium",
           "-crf", str(crf), "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", dest]
    try:
        run = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=ENCODE_TIMEOUT_S)
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"the re-encode at quality {crf} took longer than {ENCODE_TIMEOUT_S // 60} minutes "
                        "and was stopped") from exc
    if run.returncode != 0 or not os.path.isfile(dest):
        raise ToolError(f"ffmpeg could not re-encode the video: {run.stderr.strip()[:200]}")


def build_export(backend, story_id: str, episode: int, max_mib: int) -> dict:
    stories = backend.stories
    if int(max_mib) > CEILING_MAX_MIB:
        raise ToolError(f"max_mib {max_mib} is over the download ceiling ({CEILING_MAX_MIB} MiB): "
                        "comfy_download would refuse it; pick a smaller limit or copy the file with scp")
    if int(max_mib) < 1:
        raise ToolError("max_mib must be at least 1")
    limit = int(max_mib) * MIB
    try:
        final = stories.episode_file_path(story_id, episode, FINAL_NAME)
    except (KeyError, ValueError) as exc:
        raise _fail(exc) from exc
    if not os.path.isfile(final):
        raise ToolError(f"episode {episode} of story {story_id} has no {FINAL_NAME} yet: render it first")
    root = os.path.realpath(backend.outputs_dir)

    def hint(path: str) -> str:
        return f"comfy_download({os.path.relpath(os.path.realpath(path), root)})"

    size = os.path.getsize(final)
    if size <= limit:
        return {"path": os.path.relpath(os.path.realpath(final), root), "size_mib": round(size / MIB, 2),
                "crf": None, "download_hint": hint(final),
                "message": f"{FINAL_NAME} is already {size / MIB:.1f} MiB, under the {max_mib} MiB limit: "
                           "no copy needed, hand it over as it is"}
    folder = os.path.dirname(final)
    share = os.path.join(folder, SHARE_NAME)
    work = os.path.join(folder, f".{SHARE_NAME}.part.mp4")
    best = None  # (size, crf) of the smallest copy kept at `share`
    try:
        for crf in CRF_LADDER:
            _encode(final, work, crf)
            got = os.path.getsize(work)
            if best is None or got < best[0]:
                os.replace(work, share)
                best = (got, crf)
            if got <= limit:
                break
    finally:
        if os.path.exists(work):
            os.remove(work)
    got, crf = best
    answer = {"path": os.path.relpath(os.path.realpath(share), root), "size_mib": round(got / MIB, 2), "crf": crf,
              "download_hint": hint(share)}
    if got <= limit:
        answer["message"] = (f"A share copy of {got / MIB:.1f} MiB is ready (the original was "
                             f"{size / MIB:.1f} MiB; quality step {crf}, sound untouched). "
                             "Hand it over with the download_hint.")
    else:
        answer["message"] = (f"Even the lowest quality step ({crf}) leaves {got / MIB:.1f} MiB, over the "
                             f"{max_mib} MiB limit. The smallest copy is kept; comfy_download would refuse it at "
                             f"max_mib={max_mib}, so raise max_mib (up to {CEILING_MAX_MIB}) or copy it with scp.")
    return answer


def register(mcp, backend) -> None:
    @mcp.tool
    def episode_sheet(story_id: str, episode: int, columns: int = DEFAULT_COLUMNS, max_px: int = DEFAULT_SHEET_PX):
        """Free. One picture of the whole episode: every shot as a tile, in order, so you can judge the
        episode at a glance. A shot that has its clip shows a frame of the clip (the first frame, or the
        frame at 1 s when the clip lasts 2 s or more); one without shows its keyframe; one with neither is a
        grey tile. Each tile says its shot id and which of the three it is (clip, keyframe, missing).
        columns: tiles per row (default 4). max_px: the longer side of the sheet (default 1600). The
        sheet is also saved as episode_sheet.png in the episode's folder. Answers the picture and {path,
        shots: [{id, state, source}], width, height}. Refused with a sentence when the episode has no
        storyboard yet."""
        try:
            return build_sheet(backend, story_id, int(episode), columns, max_px)
        except media.MediaError as exc:
            raise ToolError(str(exc)) from exc

    @mcp.tool
    def episode_export(story_id: str, episode: int, max_mib: int = DEFAULT_MAX_MIB) -> dict:
        """Free (a few minutes of this server's processor when a copy is needed). A copy of the episode's
        finished video small enough to hand over in the chat. If episode_final.mp4 is already under
        max_mib MiB (default 25; at most 50, comfy_download's ceiling) it is answered as it is. Otherwise
        it is re-encoded in steps of lower quality (same picture size, sound untouched) into
        episode_share.mp4 next to it until it fits; if even the lowest step does not fit, the smallest copy
        is kept and the answer says so. The original is never touched. Answers {path, size_mib, crf,
        download_hint, message}: call comfy_download with the path of download_hint to get the file.
        Refused when the video does not exist yet or max_mib is over the ceiling."""
        try:
            return build_export(backend, story_id, int(episode), max_mib)
        except media.MediaError as exc:
            raise ToolError(str(exc)) from exc
