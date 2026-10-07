#!/bin/sh
# Pre-fetch the Wan 2.2 S2V weights (Comfy-Org/Wan_2.2_ComfyUI_Repackaged, Apache-2.0,
# about 18.2 GB) onto the network volume for templates/workflows/s2v_wan22.json (plan 32).
#
#   sh fetch_weights_s2v.sh                    # dev pod: volume mounted at /workspace
#   sh fetch_weights_s2v.sh /runpod-volume/models
#                                              # inside the worker container
#
# The argument is the volume's models folder (default /workspace/models). Creates
# unet/, audio_encoders/ and loras/ under it; files already present are skipped. The
# diffusion model goes under unet/ (the repo's split_files/diffusion_models/): the
# volume and the worker's extra_model_paths.yaml use unet/, which ComfyUI's UNETLoader
# reads like diffusion_models/. umt5_xxl_fp8_e4m3fn_scaled.safetensors and wan_2.1_vae.safetensors are
# already on the volume (the Wan 2.2 I2V workflow uses them) and are not fetched here.
# Uses the `hf` CLI when installed (the old `huggingface-cli` shim exits 1 on the dev
# pod), plain curl otherwise. Kept apart from fetch_weights.sh so the Chatterbox
# default stays untouched.
set -eu

MODELS=${1:-/workspace/models}
REPO=Comfy-Org/Wan_2.2_ComfyUI_Repackaged
# <folder on the volume>:<path in the repo under split_files/>
FILES="unet:diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors
audio_encoders:audio_encoders/wav2vec2_large_english_fp16.safetensors
loras:loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors"

for entry in $FILES; do
    folder=${entry%%:*}
    f=${entry#*:}
    dest="$MODELS/$folder/$(basename "$f")"
    mkdir -p "$MODELS/$folder"
    if [ -s "$dest" ]; then
        echo "skip (present): $dest"
    elif command -v hf >/dev/null 2>&1; then
        # --local-dir TARGET keeps the repo path: split_files/<f> lands under TARGET/split_files
        tmp="$MODELS/.s2v_fetch"
        hf download "$REPO" "split_files/$f" --local-dir "$tmp"
        mv "$tmp/split_files/$f" "$dest"
    else
        echo "download: $f"
        # -f: fail on HTTP errors; download to .part so a broken run leaves no half file
        curl -fL --retry 3 -o "$dest.part" \
            "https://huggingface.co/$REPO/resolve/main/split_files/$f"
        mv "$dest.part" "$dest"
    fi
done
rm -rf "$MODELS/.s2v_fetch"

echo "--- $MODELS"
for entry in $FILES; do
    folder=${entry%%:*}
    dest="$MODELS/$folder/$(basename "${entry#*:}")"
    if [ -s "$dest" ]; then
        ls -l "$dest" | awk '{printf "%12d  %s\n", $5, $9}'
    else
        echo "MISSING: $dest" >&2
        exit 1
    fi
done
