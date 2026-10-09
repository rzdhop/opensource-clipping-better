# showrunner `video` endpoint — settings (plan 36, D2)

| Setting | Value | Why |
|---|---|---|
| Image | `ghcr.io/rzdhop/showrunner-worker:0.1.0` (this folder's Dockerfile, `FROM ghcr.io/rzdhop/worker-comfyui-tts`) | the repo's plan-31 image (worker-comfyui 5.10.0 = ComfyUI 0.34.0, Chatterbox, the audio handler patch) + the LTX model folders mapped; all LTX nodes are core |
| GPU | **L40S 48 GB** (1 GPU / worker) | LTX-2.5 int8 + Gemma 4 int8 fit; cheapest working tier (D2). |
| Network volume | ≥ 150 GB, same datacenter, mounted at `/runpod-volume`, filled by `fill_volume.sh` | weights are not in the image |
| Container disk | 30 GB | image + ComfyUI temp only |
| Max workers | 3 (raise to 5 for a full episode's clip fan-out) | each clip is one independent job |
| Idle timeout | 180 s | one warm worker serves a whole batch; models stay in VRAM between jobs |
| Execution timeout | 1200 s | a 10 s clip two-stage on L40S ≈ 60–180 s; leave room for the first-load |
| FlashBoot | on | sub-10 s restarts on the same host |
| Scaler | Request count, 1 | lowest latency for a review loop |
| Env `BUCKET_ENDPOINT_URL` / `BUCKET_ACCESS_KEY_ID` / `BUCKET_SECRET_ACCESS_KEY` | your R2/S3 bucket | clips come back as URLs, never near the 20 MB response cap |
| Env `COMFY_LOG_LEVEL` | `INFO` | RunPod throttles DEBUG-level log floods |

The endpoint runs the clips (`ltx25_i2v_speech`) and the voice conversion (`vc_chatterbox`, Chatterbox is in the
base image); the pictures run on the images endpoint (`RUNPOD_IMAGE_ENDPOINT_ID`, FLUX.2 klein). Never mix image and
video models on one endpoint: every job would reload 30+ GB.

Smoke test after deploy: from a chat with the connector, one keyframe, one clip, `verify_take`, `vc_clip`,
`assemble_episode` on a scratch story (`docs/MCP.md`); the job journal shows the billed seconds.
