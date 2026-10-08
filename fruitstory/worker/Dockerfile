# fruitstory video worker — RunPod Serverless image for the LTX speech clips (plan 36, stage 0).
#
# Base: the repo's own worker image, ghcr.io/rzdhop/worker-comfyui-tts (built by
# .github/workflows/worker-tts-image.yml from docker/worker-comfyui-tts/, plan 31): that is
# runpod/worker-comfyui 5.10.0 (ComfyUI 0.34.0, CUDA 12.8, Python 3.12 in /opt/venv) plus the
# pinned Chatterbox node pack, the chatterbox/audio_encoders volume symlinks and a patched
# /handler.py that returns SaveAudio outputs under an `audio` key. ComfyUI 0.34.0 already ships
# every LTX node these workflows use (LTX-2.5 int8/convrot, Gemma 4, LTXVReferenceAudio for
# ID-LoRA, SaveVideo with audio), so nothing else is installed: cold starts are dominated by
# custom-node import time, not by model loading.
#
# Weights are NOT baked: they live on the network volume (fill_volume.sh). A 60 GB image would
# make every pull slow and invalidate FlashBoot snapshots on each rebuild.
#
# Build and push (from the repo root; or add a job to the worker-tts-image workflow):
#   docker build -t ghcr.io/rzdhop/fruitstory-worker:0.1.0 -f fruitstory/worker/Dockerfile fruitstory/worker
#   docker push ghcr.io/rzdhop/fruitstory-worker:0.1.0

ARG BASE_TAG=latest
FROM ghcr.io/rzdhop/worker-comfyui-tts:${BASE_TAG}

# ---- the volume mapping: the stock file maps only checkpoints/clip/vae/unet/loras ----
# Ours adds diffusion_models, text_encoders, latent_upscale_models (the folders the official
# LTX templates name) — audio_encoders and chatterbox are already symlinked by the base image.
COPY extra_model_paths.yaml /comfyui/extra_model_paths.yaml

ENV COMFY_LOG_LEVEL=INFO

# ---- sanity: the venv still imports ComfyUI's own deps, the handler patch is present ----
RUN /opt/venv/bin/python -c "import torch, transformers, safetensors; print('torch', torch.__version__, 'transformers', transformers.__version__)" \
    && grep -q 'rzdhop: audio outputs' /handler.py \
    && test -L /comfyui/models/chatterbox
