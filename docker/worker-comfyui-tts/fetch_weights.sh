#!/bin/sh
# Pre-fetch the Chatterbox Multilingual weights (ResembleAI/chatterbox, MIT, ~3.2 GB)
# onto the network volume so the first TTS job does not download them itself.
#
#   sh fetch_weights.sh                       # dev pod: volume mounted at /workspace
#   sh fetch_weights.sh /runpod-volume/models/chatterbox/chatterbox_multilingual
#                                             # inside the worker container
#
# Creates the folder (the worker's /comfyui/models/chatterbox is a symlink to
# <volume>/models/chatterbox, which must exist). Files already present are skipped.
# Uses huggingface-cli when installed, plain curl otherwise.
set -eu

DEST=${1:-/workspace/models/chatterbox/chatterbox_multilingual}
REPO=ResembleAI/chatterbox
FILES="ve.pt t3_mtl23ls_v2.safetensors s3gen.pt grapheme_mtl_merged_expanded_v1.json conds.pt Cangjie5_TC.json"

mkdir -p "$DEST"

missing=""
for f in $FILES; do
    if [ -s "$DEST/$f" ]; then
        echo "skip (present): $f"
    else
        missing="$missing $f"
    fi
done

if [ -n "$missing" ]; then
    if command -v huggingface-cli >/dev/null 2>&1; then
        # shellcheck disable=SC2086
        huggingface-cli download "$REPO" $missing --local-dir "$DEST"
    else
        for f in $missing; do
            echo "download: $f"
            # -f: fail on HTTP errors; download to .part so a broken run leaves no half file
            curl -fL --retry 3 -o "$DEST/$f.part" \
                "https://huggingface.co/$REPO/resolve/main/$f"
            mv "$DEST/$f.part" "$DEST/$f"
        done
    fi
fi

echo "--- $DEST"
for f in $FILES; do
    if [ -s "$DEST/$f" ]; then
        ls -l "$DEST/$f" | awk '{printf "%12d  %s\n", $5, $9}'
    else
        echo "MISSING: $f" >&2
        exit 1
    fi
done
