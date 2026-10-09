#!/usr/bin/env bash
# showrunner — fill the RunPod network volume with the weights of the showrunner workflows.
#
# Run this ONCE from a cheap pod (any GPU or CPU pod) that has the network volume mounted at
# /runpod-volume, in the same datacenter as the serverless endpoint:
#
#   export HF_TOKEN=hf_...        # a Read token of an account that accepted the license on
#                                 # https://huggingface.co/Lightricks/LTX-2.5 (the only gated repo here)
#   VOLUME_ROOT=/workspace        # on a pod the network volume is mounted at /workspace
#   bash fill_volume.sh           # idempotent: skips files already present with the right size
#
# Sizes (approx.): LTX-2.5 int8 stack 57 GB (Chatterbox 3 GB: fetch_weights.sh).
# Volume: 150 GB minimum (leave room for the Flux 2 Klein / Qwen-Image-Edit stack of the
# images endpoint if it shares the volume).

set -euo pipefail

ROOT="${VOLUME_ROOT:-/runpod-volume}"
MODELS="$ROOT/models"
: "${HF_TOKEN:?HF_TOKEN is required (Lightricks/LTX-2.5 is gated)}"

mkdir -p "$MODELS"/{diffusion_models,text_encoders,vae,latent_upscale_models,checkpoints,loras}

# fetch <url> <target path>: resumable, skips a complete file.
fetch() {
  local url="$1" dest="$2"
  if [ -s "$dest" ]; then
    echo "skip   $(basename "$dest") (present)"
    return 0
  fi
  echo "fetch  $(basename "$dest")"
  curl -L --fail --retry 5 --retry-delay 10 -C - \
       -H "Authorization: Bearer $HF_TOKEN" \
       -o "$dest.part" "$url"
  mv "$dest.part" "$dest"
}

# ---------------------------------------------------------------- LTX-2.5 (paths a and b)
LTX25="https://huggingface.co/Lightricks/LTX-2.5/resolve/main"
fetch "$LTX25/diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors" \
      "$MODELS/diffusion_models/ltx-2.5-22b-distilled-transformer-comfy-int8-convrot.safetensors"
fetch "$LTX25/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors" \
      "$MODELS/text_encoders/gemma4-12b-with-proj-ltx-2.5-comfy-int8-convrot.safetensors"
fetch "$LTX25/vae/ltx-2.5-video-vae-bf16.safetensors" "$MODELS/vae/ltx-2.5-video-vae-bf16.safetensors"
fetch "$LTX25/vae/ltx-2.5-audio-vae-bf16.safetensors" "$MODELS/vae/ltx-2.5-audio-vae-bf16.safetensors"
fetch "$LTX25/latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors" \
      "$MODELS/latent_upscale_models/ltx-2.5-latent-spatial-upscaler-x2-bf16-1.0.safetensors"

# ---------------------------------------------------------------- Chatterbox (voice conversion)
# The node reads <ComfyUI>/models/chatterbox/chatterbox_multilingual/, symlinked to the volume by
# the base image (docker/worker-comfyui-tts). Its weights are fetched by the repo's own script:
#   sh docker/worker-comfyui-tts/fetch_weights.sh      (destination: $ROOT/models/chatterbox)
if [ -d "$MODELS/chatterbox/chatterbox_multilingual" ]; then
  echo "skip   chatterbox (present)"
else
  echo "todo   chatterbox: run docker/worker-comfyui-tts/fetch_weights.sh on this volume"
fi

echo
echo "Volume contents:"
du -sh "$MODELS"/* 2>/dev/null || true
echo "done"
