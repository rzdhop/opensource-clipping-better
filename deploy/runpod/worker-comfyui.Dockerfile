# The serverless worker image for the video endpoint when LTX-2.5 needs a ComfyUI
# newer than the one runpod/worker-comfyui ships (DEC-312; runbook 11-INFRA §6).
#
#   docker build -f deploy/runpod/worker-comfyui.Dockerfile -t <you>/worker-comfyui:ltx25 .
#   docker push <you>/worker-comfyui:ltx25
#
# then point the endpoint's "Container image" at it. The base image already
# carries the handler, comfy-cli and the extra_model_paths.yaml that maps the
# network volume (/runpod-volume/models/<type>); only ComfyUI itself moves.
# The 5090 needs CUDA >= 12.8, hence the cuda12.8.1 base tag.
ARG BASE_TAG=5.10.0-base-cuda12.8.1
FROM runpod/worker-comfyui:${BASE_TAG}

# The ComfyUI release to run: a tag of github.com/comfyanonymous/ComfyUI (v0.3x.y),
# the first one whose template browser lists "LTX-2.5 Image to Video".
ARG COMFYUI_VERSION=latest

# comfy-cli installed ComfyUI as a git checkout in /comfyui; move it to the tag
# (or the newest tag) and refresh its Python requirements inside the same venv.
RUN set -eux; cd /comfyui; git fetch --tags --depth=1 origin; \
    if [ "${COMFYUI_VERSION}" = "latest" ]; then tag="$(git tag --sort=-v:refname | head -n 1)"; else tag="v${COMFYUI_VERSION#v}"; fi; \
    git checkout -q "${tag}"; \
    pip install --no-cache-dir -r requirements.txt; \
    python -c "import comfyui_version; print('ComfyUI', comfyui_version.__version__)" || true
