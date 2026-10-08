# showrunner `video` endpoint — settings (plan 36, D2)

| Setting | Value | Why |
|---|---|---|
| Image | `ghcr.io/rzdhop/showrunner-worker:0.1.0` (this folder's Dockerfile, `FROM ghcr.io/rzdhop/worker-comfyui-tts`) | the repo's plan-31 image (worker-comfyui 5.10.0 = ComfyUI 0.34.0, Chatterbox, the audio handler patch) + the LTX model folders mapped; all LTX nodes are core |
| GPU | **L40S 48 GB** (1 GPU / worker) | LTX-2.5 int8 + Gemma 4 int8 fit; cheapest working tier (D2). ID-LoRA 2.3 fp8 (27 GB) + Gemma 3 (8.8 GB) also fit. |
| Network volume | ≥ 150 GB, same datacenter, mounted at `/runpod-volume`, filled by `fill_volume.sh` | weights are not in the image |
| Container disk | 30 GB | image + ComfyUI temp only |
| Max workers | 3 (raise to 5 for a full episode's clip fan-out) | each clip is one independent job |
| Idle timeout | 180 s | one warm worker serves a whole batch; models stay in VRAM between jobs |
| Execution timeout | 1200 s | a 10 s clip two-stage on L40S ≈ 60–180 s; ID-LoRA slower; leave room for the first-load |
| FlashBoot | on | sub-10 s restarts on the same host |
| Scaler | Request count, 1 | lowest latency for a review loop |
| Env `BUCKET_ENDPOINT_URL` / `BUCKET_ACCESS_KEY_ID` / `BUCKET_SECRET_ACCESS_KEY` | your R2/S3 bucket | clips come back as URLs, never near the 20 MB response cap |
| Env `COMFY_LOG_LEVEL` | `INFO` | RunPod throttles DEBUG-level log floods |

The app already has three endpoints (`docs/MCP.md`): **video** (`RUNPOD_COMFY_ENDPOINT_ID`, Wan 2.2 / LTX-2), **image** (`RUNPOD_IMAGE_ENDPOINT_ID`, FLUX.2 klein, same volume) and **voice** (`RUNPOD_AUDIO_ENDPOINT_ID`, the tts image). Stage 0 uses a **fourth endpoint** on this image, on the same volume (never switch the live **video** endpoint: the app's S2V talking clips run on it, and they are the comparison) and the **image** endpoint for `keyframe3`; TTS lines can run on the video endpoint (Chatterbox is in the base image) or on the voice endpoint. Never mix image and video models on one endpoint: every job would reload 30+ GB.

Smoke test after deploy (from the repo root, `RUNPOD_API_KEY` in the environment or `.env`):

```
python -m showrunner.stage0.run_stage0 smoke --video-endpoint <id>
```

It submits one 5 s `ltx25_i2v_speech` clip and prints the billed seconds.
