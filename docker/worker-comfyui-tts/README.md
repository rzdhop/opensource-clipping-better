# worker-comfyui-tts

The RunPod serverless ComfyUI worker image with **Chatterbox Multilingual TTS** added
(plan 31). It is `runpod/worker-comfyui:5.10.0-base-cuda12.8.1` (the pin of
`deploy/runpod/worker-comfyui.Dockerfile`) plus four additive changes. The base already
carries the ComfyUI the endpoints run today; ComfyUI does not move, so the existing
image and video workflow templates keep working unchanged.

## What the image adds

| What | How | Why |
|---|---|---|
| The `FL_ChatterboxMultilingualTTS` node | `git clone` of `filliptm/ComfyUI_Fill-ChatterBox` into `/comfyui/custom_nodes`, pinned to commit `f7d7a16187430abcaf91a3039b9c83aa9960816a` (v1.0.5, 2026-08-24, "Fix optional Perth imports"), `uv pip install -r requirements.txt` into `/opt/venv` | The registry copy (`comfy-node-install`) is 1.0.4 and needs `resemble-perth`; the pin makes the build reproducible. Chatterbox is vendored in the pack, so nothing pins torch; the build compares `torch.__version__` before and after the install and fails if it moved. |
| A symlink `/comfyui/models/chatterbox -> /runpod-volume/models/chatterbox` | `ln -s` in the Dockerfile | The node reads its weights from `<ComfyUI>/models/chatterbox/chatterbox_multilingual/` (its own path, not `extra_model_paths.yaml`). The link puts them on the network volume. |
| A symlink `/comfyui/models/audio_encoders -> /runpod-volume/models/audio_encoders` | `ln -s` in the Dockerfile | The worker's `extra_model_paths.yaml` does not map the `audio_encoders` folder, which the Wan 2.2 S2V workflow (`s2v_wan22`, below) reads through core `AudioEncoderLoader`. The link puts it on the network volume. |
| A patched `/handler.py` | `patch_handler.py` run at build time | SaveAudio's `audio` output is returned like images (below). |

Input of the node: `text`, `language` (`"French (fr)"`), `exaggeration`, `cfg_weight`,
`temperature`, `repetition_penalty`, `min_p`, `top_p`, `seed`, and an optional
`audio_prompt` (AUDIO, at least 6 s) for the reference voice.

## Weights: on the volume, not in the image

`ResembleAI/chatterbox` (MIT), about 3.2 GB: `ve.pt` (5.7 MB), `t3_mtl23ls_v2.safetensors`
(2.14 GB), `s3gen.pt` (1.06 GB), `grapheme_mtl_merged_expanded_v1.json`, `conds.pt`,
`Cangjie5_TC.json` (1.9 MB). They live on the network volume next to every other model;
the image stays the base's size and the CI build stays light.

- **Recommended: pre-fetch once** from the dev pod (the same volume, mounted at
  `/workspace`): `sh docker/worker-comfyui-tts/fetch_weights.sh` (default destination
  `/workspace/models/chatterbox/chatterbox_multilingual`; pass another path as the first
  argument, for instance inside a worker container). It creates the folder, skips files
  already present, uses `huggingface-cli` when installed and `curl` otherwise, and prints
  the sizes.
- **First-run behaviour otherwise:** the node downloads the files itself on the first job
  (about 2 minutes on top of the cold start). That needs `<volume>/models/chatterbox` to
  exist: with the symlink dangling, a `makedirs` through it can fail. Running
  `fetch_weights.sh` once, or `mkdir -p /workspace/models/chatterbox` on the dev pod, avoids
  the question.

## Wan 2.2 S2V weights (plan 32): a talking clip from a keyframe and a voice line

The workflow template `clipping/aistory/templates/workflows/s2v_wan22.json` (image + one spoken
line in, a clip of up to 5 s out, the mouth following the voice) runs on core ComfyUI nodes only
(`WanSoundImageToVideo`, `AudioEncoderLoader`, `AudioEncoderEncode`, in ComfyUI since v0.3.53;
the base's ComfyUI is newer). It adds no node pack; it needs the three files below on the
volume, next to the Wan 2.2 I2V files already there (`umt5_xxl_fp8_e4m3fn_scaled.safetensors` and
`wan_2.1_vae.safetensors` are shared and not fetched again).
`Comfy-Org/Wan_2.2_ComfyUI_Repackaged` (Apache-2.0), `split_files/`:

| File | Size | Goes in |
|---|---|---|
| `diffusion_models/wan2.2_s2v_14B_fp8_scaled.safetensors` | 16.4 GB | `models/diffusion_models` |
| `audio_encoders/wav2vec2_large_english_fp16.safetensors` | 0.63 GB | `models/audio_encoders` (the new symlink) |
| `loras/wan2.2_t2v_lightx2v_4steps_lora_v1.1_high_noise.safetensors` | 1.2 GB | `models/loras` (the T2V v1.1 LoRA, not the two I2V ones already there) |

Pre-fetch once from the dev pod: `sh docker/worker-comfyui-tts/fetch_weights_s2v.sh` (default
`/workspace/models`; pass `/runpod-volume/models` inside a worker container). Same behaviour
as `fetch_weights.sh`: skips files present, `huggingface-cli` or `curl`, prints the sizes. The
Chatterbox script is untouched.

How the graph works: the voice line goes through `LoadAudio` (a WAV uploaded like a keyframe) and
`AudioEncoderEncode` (resampled to 16 kHz, channels averaged); `WanSoundImageToVideo` takes the
keyframe (`ref_image`, center-cropped to width x height; 480x832 is valid, both are multiples of
16) and one 77-frame chunk; 4 steps, cfg 1, `uni_pc`/`simple`, shift 8, with the lightx2v LoRA.
**First-frame note:** ComfyUI's own graph doubles the first latent frame (`LatentCut` +
`LatentConcat`) and drops the first 3 decoded frames (`ImageFromBatch`) because the VAE
overbakes the first frame. One chunk decodes to 81 frames, 78 remain (4.9 s at 16 fps); audio
beyond the clip is ignored, a shorter line leaves the mouth idle. The template's input names
were checked against the ComfyUI v0.34.0 source, not against a running ComfyUI.

Status: `verified_live` is false. **Unverified on cartoon faces and on French: the human's
one-line test on a fruit keyframe gates its use**; nothing in the clips pipeline calls it yet.
Rebuild the image (CI) before the test: the `audio_encoders` link is new.

## The handler patch

`worker-comfyui`'s handler collects only the `images` key of each node output and logs the
rest as "unhandled". `patch_handler.py` (stdlib only, `python3 /patch_handler.py /handler.py`)
makes `audio` (core `SaveAudio`: `audio: [{filename, subfolder, type}]`, `.flac` or `.wav`)
travel the same road as images: same `/view` fetch, same `temp` skip, same base64 or S3
branch, same item shape. The result is:

```json
{
  "images": [],
  "audio": [{"filename": "line_00001_.flac", "type": "base64", "data": "<base64>"}]
}
```

(`type` is `"s3_url"` and `data` the URL when the endpoint has `BUCKET_ENDPOINT_URL`.)
`images` is unchanged; `audio` is present only when the job produced some. A job with only
audio is not reported as `success_no_images`: that status needs both lists empty.

The patch is a handful of exact-string replacements, each asserted present exactly once; on
an unexpected handler it exits with the missing anchor named and the image build fails. It
is idempotent (marker comment `# rzdhop: audio outputs`). `tests/test_worker_tts_handler_patch.py`
applies it to a verbatim excerpt of the 5.10.0 handler and runs the result.

The handler source was taken from
`https://raw.githubusercontent.com/runpod-workers/worker-comfyui/5.10.0/handler.py` (the tag
`5.10.0`; no fallback to `v5.10.0` was needed).

## Licences

| Part | Licence | Note |
|---|---|---|
| `filliptm/ComfyUI_Fill-ChatterBox` (node pack) | MIT per its README | **No LICENSE file in the repo.** Accepted risk (A-197). |
| Chatterbox code (vendored in the pack) | MIT (Resemble AI) | |
| Weights `ResembleAI/chatterbox` | MIT | |
| `runpod-workers/worker-comfyui` (base image, `/handler.py`) | **AGPL-3.0** | The image carries a modified handler. This public repo, holding this Dockerfile and `patch_handler.py`, is the source offer for the change; the GHCR package is labelled `org.opencontainers.image.licenses=AGPL-3.0` and links to the repo. |

## Build

Locally (about 11 GB base image):

```sh
docker build -t ghcr.io/rzdhop/worker-comfyui-tts:dev docker/worker-comfyui-tts
```

`--build-arg BASE_TAG=...` changes the base; the default is `5.10.0-base-cuda12.8.1`. A
different base handler may need new anchors in `patch_handler.py`.

In CI: `.github/workflows/worker-tts-image.yml` (name "worker-comfyui-tts image") runs on
`workflow_dispatch` (optional input `base_tag`) and on pushes touching `docker/worker-comfyui-tts/**`
or the workflow itself. It frees runner disk, logs in to GHCR with `GITHUB_TOKEN` and pushes
`ghcr.io/rzdhop/worker-comfyui-tts:<sha>` and `:latest`. No other secret, no layer cache
(the layers are too big for the 10 GB Actions cache).

## Using it on RunPod

1. Make the package pullable: after the first push, GitHub -> Packages ->
   `worker-comfyui-tts` -> Package settings -> Change visibility -> Public (the image holds
   no secret). Or keep it private and add a registry credential on RunPod (Settings ->
   Container Registry Auth, a GitHub token with `read:packages`).
2. Point the IMAGE endpoint (`RUNPOD_IMAGE_ENDPOINT_ID`, `aq6qg1pykxa2st`) at
   `ghcr.io/rzdhop/worker-comfyui-tts:latest` (Container image field), keeping its network
   volume attached. Everything the endpoint ran before still runs: only the node, the symlink
   and the handler's extra `audio` key are new.

Cost reference (RTX 5090 flex, $0.00044/s): a line is about 8 GPU-s warm, about $0.004; a
cold start is about 90 s, about $0.04; the first-run weights download, if not pre-fetched,
is about 2 minutes of one job.

## To verify on the first live run

- The vendored T3 works with the `transformers` version the base image carries (the pack's
  requirements are unpinned, so the install may or may not have moved it).
- The patched handler returns `audio` with `.flac` items for a `SaveAudio` workflow.
- The worker's `images` upload (the input-file path of `worker-comfyui`) accepts a `.wav`
  that `LoadAudio` then finds in `/comfyui/input` (the reference voice).
- The node's weight download works through the symlink when the folder is missing (otherwise
  use `fetch_weights.sh`).
- The node file the Dockerfile asserts, `chatterbox_node.py`, exists at the pinned commit
  (the build's smoke check fails fast if it does not).
