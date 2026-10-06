#!/bin/sh
# Pre-fetch the Wan 2.2 S2V weights (Comfy-Org/Wan_2.2_ComfyUI_Repackaged, Apache-2.0,
# about 18.2 GB) onto the network volume for templates/workflows/s2v_wan22.json (plan 32).
#
#   sh fetch_weights_s2v.sh                    # dev pod: volume mounted at /workspace
#   sh fetch_weights_s2v.sh /runpod-volume/models
#                                              # inside the worker container
#
# The argument is the volume's models folder (default /workspace/models). Creates
# diffusion_models/, audio_encoders/ and loras/ under it; files already present are
# skipped. umt5_xxl_fp8_e4m3fn_scaled.safetensors and wan_2.1_vae.safetensors are
# already on the volume (the Wan 2.2 I2V workflow uses them) and are not fetched here.
# Uses huggingface-cli when installed, plain curl otherwise. Kept apart from
# fetch_weights.sh so the Chatterbox default stays untouched.
set -eu

MODELS=${1:-/workspace/models}
REPO=Comfy-Org/Wan_2.2_ComfyUI_Repackaged
# <path in the repo under split_files/>  (the folder name is the same on the volume)
FILES="diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors
audio_encoders/wav2vec2_large_english_fp16.safetensors
loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"

for f in $FILES; do
    mkdir -p "$MODELS/$(dirname "$f")"
    if [ -s "$MODELS/$f" ]; then
        echo "skip (present): $f"
    elif command -v huggingface-cli >/dev/null 2>&1; then
        # --local-dir TARGET keeps the repo path: split_files/<f> lands under TARGET/split_files
        tmp="$MODELS/.s2v_fetch"
        huggingface-cli download "$REPO" "split_files/$f" --local-dir "$tmp"
        mv "$tmp/split_files/$f" "$MODELS/$f"
    else
        echo "download: $f"
        # -f: fail on HTTP errors; download to .part so a broken run leaves no half file
        curl -fL --retry 3 -o "$MODELS/$f.part" \
            "https://huggingface.co/$REPO/resolve/main/split_files/$f"
        mv "$MODELS/$f.part" "$MODELS/$f"
    fi
done
rm -rf "$MODELS/.s2v_fetch"

echo "--- $MODELS"
for f in $FILES; do
    if [ -s "$MODELS/$f" ]; then
        ls -l "$MODELS/$f" | awk '{printf "%12d  %s\n", $5, $9}'
    else
        echo "MISSING: $f" >&2
        exit 1
    fi
done
