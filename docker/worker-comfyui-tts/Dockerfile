# The serverless worker image with Chatterbox Multilingual TTS added (plan 31).
#
#   docker build -t ghcr.io/rzdhop/worker-comfyui-tts:dev docker/worker-comfyui-tts
#
# CI (.github/workflows/worker-tts-image.yml) builds and pushes it to GHCR; then
# point the endpoint's "Container image" at ghcr.io/rzdhop/worker-comfyui-tts:latest.
# See README.md for the licences, the weights-on-volume choice and the first run.
#
# Everything is additive on top of the base the endpoints already run: ComfyUI
# itself does not move (no version change), the base keeps its handler start,
# comfy-cli and extra_model_paths.yaml (volume mounted at /runpod-volume). Three
# things are added: the ComfyUI_Fill-ChatterBox node pack (pinned commit), a symlink
# that puts the Chatterbox weights on the network volume, and a build-time patch of
# /handler.py so SaveAudio outputs come back like images.
# The 5090 / RTX PRO 6000 need CUDA >= 12.8, hence the cuda12.8.1 base tag (the same
# pin as deploy/runpod/worker-comfyui.Dockerfile).
ARG BASE_TAG=5.10.0-base-cuda12.8.1
FROM runpod/worker-comfyui:${BASE_TAG}

# filliptm/ComfyUI_Fill-ChatterBox v1.0.5 (2026-08-24, "Fix optional Perth imports").
# Cloned and pinned instead of `comfy-node-install`: the registry copy is 1.0.4 and
# needs resemble-perth. Chatterbox is vendored in the pack, so there is no torch pin.
ARG CHATTERBOX_NODE_REPO=https://github.com/filliptm/ComfyUI_Fill-ChatterBox
ARG CHATTERBOX_NODE_SHA=f7d7a16187430abcaf91a3039b9c83aa9960816a

# The pack's requirements are unpinned (numpy, resampy, librosa, s3tokenizer,
# transformers, diffusers, omegaconf, conformer, safetensors, soundfile): the build
# fails if the install moves torch, because torch and CUDA are the base's contract.
# Packages live in the uv venv at /opt/venv (on PATH).
RUN set -eux; \
    git --version; \
    torch_before="$(python -c 'import torch; print(torch.__version__)')"; \
    git clone "${CHATTERBOX_NODE_REPO}" /comfyui/custom_nodes/ComfyUI_Fill-ChatterBox; \
    cd /comfyui/custom_nodes/ComfyUI_Fill-ChatterBox; \
    git checkout "${CHATTERBOX_NODE_SHA}"; \
    if command -v uv >/dev/null 2>&1; then \
        uv pip install --python /opt/venv/bin/python -r requirements.txt; \
    else \
        python -m pip install --no-cache-dir -r requirements.txt; \
    fi; \
    torch_after="$(python -c 'import torch; print(torch.__version__)')"; \
    echo "torch: ${torch_before} -> ${torch_after}"; \
    test "${torch_before}" = "${torch_after}"

# Weights live on the network volume, not in the image (~3.2 GB, ResembleAI/chatterbox,
# MIT). The node reads <ComfyUI>/models/chatterbox/chatterbox_multilingual/ and
# downloads the files itself when missing, so this symlink makes that path the volume's.
# The link dangles at build time and in a container without the volume: expected.
RUN rm -rf /comfyui/models/chatterbox \
    && ln -s /runpod-volume/models/chatterbox /comfyui/models/chatterbox

# /handler.py (AGPL-3.0, runpod-workers/worker-comfyui 5.10.0): collect the `audio`
# key of node outputs like `images`. The patch fails the build on an unexpected handler.
COPY patch_handler.py /patch_handler.py
RUN test -f /handler.py && python3 /patch_handler.py /handler.py

# Cheap build-time checks: the node file is where ComfyUI will load it, the handler
# carries the patch. (The node itself needs the running server, so no import here.)
RUN python -c "import pathlib; assert pathlib.Path('/comfyui/custom_nodes/ComfyUI_Fill-ChatterBox/chatterbox_node.py').exists()"
RUN grep -q 'rzdhop: audio outputs' /handler.py
