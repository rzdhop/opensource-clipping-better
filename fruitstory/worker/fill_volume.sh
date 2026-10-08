#!/usr/bin/env bash
# fruitstory — fill the RunPod network volume with the weights of the stage-0 workflows.
#
# Run this ONCE from a cheap pod (any GPU or CPU pod) that has the network volume mounted at
# /runpod-volume, in the same datacenter as the serverless endpoint:
#
#   export HF_TOKEN=hf_...        # a token with "read access to gated repos":
#                                 # accept the license on https://huggingface.co/Lightricks/LTX-2.5
#                                 # and https://huggingface.co/Lightricks/LTX-2.3-fp8 first.
#   bash fill_volume.sh           # idempotent: skips files already present with the right size
#
# Sizes (approx.): LTX-2.5 int8 stack 57 GB, LTX-2.3 ID-LoRA stack 40 GB (Chatterbox 3 GB: fetch_weights.sh).
# Volume: 150 GB minimum (leave room for the Flux 2 Klein / Qwen-Image-Edit stack of the
# images endpoint if it shares the volume).

set -euo pipefail

ROOT="${VOLUME_ROOT:-/runpod-volume}"
MODELS="$ROOT/models"
: "${HF_TOKEN:?HF_TOKEN is required (gated Lightricks repos)}"

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

# ---------------------------------------------------------------- LTX-2.3 ID-LoRA (path c)
fetch "https://huggingface.co/Lightricks/LTX-2.3-fp8/resolve/main/ltx-2.3-22b-dev-fp8.safetensors" \
      "$MODELS/checkpoints/ltx-2.3-22b-dev-fp8.safetensors"
fetch "https://huggingface.co/Comfy-Org/ltx-2.3/resolve/main/split_files/loras/ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors" \
      "$MODELS/loras/ltx_2.3_22b_distilled_1.1_lora_dynamic_fro09_avg_rank_111_bf16.safetensors"
fetch "https://huggingface.co/Comfy-Org/ltx-2.3/resolve/main/split_files/loras/ltx-2.3-id-lora-talkvid-3k.safetensors" \
      "$MODELS/loras/ltx-2.3-id-lora-talkvid-3k.safetensors"
fetch "https://huggingface.co/Comfy-Org/ltx-2/resolve/main/split_files/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors" \
      "$MODELS/text_encoders/gemma_3_12B_it_fp4_mixed.safetensors"
fetch "https://huggingface.co/Lightricks/LTX-2.3/resolve/main/ltx-2.3-spatial-upscaler-x2-1.1.safetensors" \
      "$MODELS/latent_upscale_models/ltx-2.3-spatial-upscaler-x2-1.1.safetensors"

# ---------------------------------------------------------------- Chatterbox (TTS lines, path b)
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
