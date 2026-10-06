#!/usr/bin/env python3
"""Build-time patch of runpod-workers/worker-comfyui's /handler.py (tag 5.10.0).

Why: the stock handler only collects the ``images`` key of each ComfyUI node
output; core SaveAudio reports ``audio: [{filename, subfolder, type}]`` and the
stock handler logs it as "unhandled" and drops it. This patch collects the
``audio`` entries exactly like images (same /view fetch through
``get_image_data``, same ``temp`` skip, same base64 / S3 branches, same item
shape ``{"filename", "type", "data"}``) and returns them under their own key::

    {"images": [...], "audio": [{"filename": "x.flac", "type": "base64", "data": "..."}]}

The ``images`` key is unchanged. A job with only audio is not the empty case:
``status: success_no_images`` is only reported when both lists are empty.

How it stays small: the output loop is not rewritten. Before it, a list of
(sink, node_id, node_output) views is built: every node once with its own
output going to the images list, and every node that has ``audio`` once more
with ``{"images": <its audio entries>}`` going to the audio list. The loop
header then rebinds ``output_data`` to the view's sink, so the stock loop body
runs unchanged for both kinds.

The anchors are exact strings, each asserted present exactly once; on any
mismatch the script exits non-zero naming the anchor, so an image build against
an unexpected handler fails loudly instead of shipping a silent no-op.
Idempotent: a patched file carries the marker below and is left alone.

Usage (build time): python3 /patch_handler.py /handler.py
Licence note: /handler.py is AGPL-3.0 (runpod-workers/worker-comfyui); this
patch and the Dockerfile in this directory are the source offer for the change.
"""

import sys

MARKER = "# rzdhop: audio outputs"

# (name, exact anchor, replacement). Order does not matter; each is replaced once.
_LOOP_ANCHOR = "        for node_id, node_output in outputs.items():\n"
_LOOP_NEW = (
    f"        {MARKER}\n"
    "        audio_data = []\n"
    "        _rz_images_data = output_data\n"
    "        _rz_views = []\n"
    "        for _rz_id, _rz_out in outputs.items():\n"
    "            _rz_views.append((_rz_images_data, _rz_id, _rz_out))\n"
    '            if _rz_out.get("audio"):\n'
    '                _rz_views.append((audio_data, _rz_id, {"images": _rz_out["audio"]}))\n'
    "        for _rz_sink, node_id, node_output in _rz_views:\n"
    "            output_data = _rz_sink\n"
)

_OTHER_ANCHOR = (
    '            other_keys = [k for k in node_output.keys() if k != "images"]\n'
)
_OTHER_NEW = (
    '            other_keys = [k for k in node_output.keys() if k not in ("images", "audio")]\n'
)

_FINAL_ANCHOR = (
    "    final_result = {}\n"
    "\n"
    "    if output_data:\n"
    '        final_result["images"] = output_data\n'
)
_FINAL_NEW = (
    "    output_data = _rz_images_data\n"
    "    final_result = {}\n"
    "\n"
    "    if output_data:\n"
    '        final_result["images"] = output_data\n'
    "    if audio_data:\n"
    '        final_result["audio"] = audio_data\n'
)

_FAIL_ANCHOR = "    if not output_data and errors:\n"
_FAIL_NEW = "    if not output_data and not audio_data and errors:\n"

_EMPTY_ANCHOR = "    elif not output_data and not errors:\n"
_EMPTY_NEW = "    elif not output_data and not audio_data and not errors:\n"

REPLACEMENTS = (
    ("output loop header", _LOOP_ANCHOR, _LOOP_NEW),
    ("unhandled-keys check", _OTHER_ANCHOR, _OTHER_NEW),
    ("final_result assembly", _FINAL_ANCHOR, _FINAL_NEW),
    ("failure branch (no images, errors)", _FAIL_ANCHOR, _FAIL_NEW),
    ("empty branch (success_no_images)", _EMPTY_ANCHOR, _EMPTY_NEW),
)


def patch_source(text: str) -> str:
    """Return the handler source with audio outputs collected and returned.

    Raises SystemExit naming the anchor if the handler is not the expected one.
    """
    if MARKER in text:
        return text
    for name, anchor, new in REPLACEMENTS:
        found = text.count(anchor)
        if found != 1:
            raise SystemExit(
                f"patch_handler: anchor for the {name} found {found} times "
                f"(expected exactly 1): {anchor!r}. "
                "The base image's handler.py is not the 5.10.0 one this patch targets."
            )
        text = text.replace(anchor, new)
    return text


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: patch_handler.py /path/to/handler.py", file=sys.stderr)
        return 2
    path = argv[1]
    with open(path, encoding="utf-8") as fh:
        original = fh.read()
    patched = patch_source(original)
    compile(patched, path, "exec")  # never write a handler that does not parse
    if patched != original:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(patched)
        print(f"patch_handler: patched {path} (audio outputs)")
    else:
        print(f"patch_handler: {path} already patched, nothing to do")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
