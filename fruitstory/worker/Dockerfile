# fruitstory video worker — RunPod Serverless image for the LTX speech clips (plan 30, stage 0).
#
# Base: runpod/worker-comfyui 5.10.0 = ComfyUI 0.34.0, CUDA 12.8, Python 3.12 in /opt/venv.
# ComfyUI 0.34.0 already ships every LTX node these workflows use (LTX-2.5 int8/convrot,
# Gemma 4 text encoder, LTXVReferenceAudio for ID-LoRA, SaveVideo with audio), so the only
# custom node pack is Chatterbox for the TTS lines of the A2V path. Keep it that way: the
# cold start is dominated by custom-node import time, not by model loading.
#
# Weights are NOT baked: they live on the network volume (see fill_volume.sh and
# extra_model_paths.yaml). A 60 GB image would make every pull slow and invalidate FlashBoot
# snapshots on each rebuild.
#
# Build and push (from the repo root):
#   docker build -t ghcr.io/<you>/fruitstory-worker:0.1.0 -f fruitstory/worker/Dockerfile fruitstory/worker
#   docker push ghcr.io/<you>/fruitstory-worker:0.1.0

FROM runpod/worker-comfyui:5.10.0-base

# ---- custom nodes (comfy-node-install surfaces install errors; comfy-cli swallows them) ----
# ComfyUI_Fill-ChatterBox: FL_ChatterboxMultilingualTTS (23 languages incl. French) and
# FL_ChatterboxDialogTTS (multi-speaker, English). Used only by tts_chatterbox_line.json.
RUN comfy-node-install comfyui_fill-chatterbox
# (registry id from its pyproject; if the registry copy lags, use the git URL instead:
#  RUN comfy-node-install https://github.com/filliptm/ComfyUI_Fill-ChatterBox)

# Chatterbox pulls its own weights from Hugging Face at first use: point every HF cache at the
# volume so a warm volume means no download on a cold worker.
ENV HF_HOME=/runpod-volume/huggingface-cache \
    HF_HUB_CACHE=/runpod-volume/huggingface-cache/hub \
    TRANSFORMERS_CACHE=/runpod-volume/huggingface-cache/hub \
    COMFY_LOG_LEVEL=INFO

# ---- the volume mapping: the stock file maps only checkpoints/clip/vae/unet/loras ----
# Ours adds diffusion_models, text_encoders, latent_upscale_models, audio_encoders, model_patches.
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml

# ---- sanity: the venv must still import ComfyUI's own deps after the node install ----
# (DR-1170: a pip install into the wrong venv makes the server unreachable at boot.)
RUN /opt/venv/bin/python -c "import torch, transformers, safetensors; print('torch', torch.__version__, 'transformers', transformers.__version__)"
