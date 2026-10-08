# fruitstory `video` endpoint — settings (plan 30, D2)

| Setting | Value | Why |
|---|---|---|
| Image | `ghcr.io/<you>/fruitstory-worker:0.1.0` (this folder's Dockerfile) | worker-comfyui 5.10.0 = ComfyUI 0.34.0, all LTX nodes core |
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

Two endpoints, not one: keep the existing **images** endpoint (Flux 2 Klein, Qwen-Image-Edit) separate from this **video** endpoint. Mixing image and video models on one worker thrashes ComfyUI's model cache (every job reloads 30+ GB).

Smoke test after deploy (from the repo root, `RUNPOD_API_KEY` in the environment or `.env`):

```
python -m fruitstory.stage0.run_stage0 --video-endpoint <id> --only smoke
```

It submits one 5 s `ltx25_i2v_speech` clip and prints the billed seconds.
