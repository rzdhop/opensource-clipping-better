# Plan 30 — CHECKPOINT (2026-10-08 12:00, cloud session linked to the Windows PC)

Resume prompt for the next session: *"Continue plan 30 stage 0 from docs/plans/30-CHECKPOINT.md"*.

## Where we are

- **Plan 30 accepted** (`docs/plans/30-rebuild-from-scratch-plan.md`, decisions D1–D6 in §7; D7 = the voice path, pending stage 0).
- **Stage 0 code COMPLETE, not yet run on a GPU** — `fruitstory/` (22 files, stdlib + ffmpeg only, no import of `clipping/aistory`):
  worker image (`worker/`), four API-format workflows validated against ComfyUI **v0.34.0** source (the version
  worker-comfyui 5.10.0 pins), the stage-0 runner and matrix, offline tests (15 green in the cloud container;
  the desktop VM has no pytest — run them on the next system).
- **Not committed to git.** Untracked: `fruitstory/`, `docs/plans/`, `productions/` (the test keyframes the matrix
  points at), `tools/runpod_smoke.py`, `.claude/settings.local.json`.

## What the next system must have

1. `RUNPOD_API_KEY` in `.env` (or the env) — the runner reads it; never printed.
2. The **video endpoint** does not exist yet: build/push `fruitstory/worker/Dockerfile` to GHCR, make a ≥ 150 GB
   network volume in the endpoint's datacenter, fill it with `fruitstory/worker/fill_volume.sh` (needs an HF token
   that accepted `Lightricks/LTX-2.5` and `Lightricks/LTX-2.3-fp8`), create the endpoint per `fruitstory/worker/ENDPOINT.md`
   (L40S 48 GB, idle 180 s, timeout 1200 s, R2/S3 env vars).
3. The **images endpoint** (Flux 2 Klein, the existing one — `e14bceyj7rrdxl` in `tools/runpod_smoke.py`) for `keyframe3`.
4. The `rzdhop-story` MCP answered **502** all session (`CLIENT_HTTP_NOT_IMPLEMENTED` dialing its URL). Not needed for
   stage 0 (the runner talks to RunPod directly); blocks stage 2.

## Stage 0 run order (from the repo root)

```
python -m pytest fruitstory/tests -q
python -m fruitstory.stage0.run_stage0 smoke     --video-endpoint <id>
python -m fruitstory.stage0.run_stage0 keyframe3 --images-endpoint <id>
python -m fruitstory.stage0.run_stage0 a         --video-endpoint <id>      # 9 single clips + 2/3-speaker exchanges
python -m fruitstory.stage0.run_stage0 review
python -m fruitstory.stage0.run_stage0 voice --character paloma --from stories/_stage0/a/paloma_fr_s22.mp4   # x3 characters
python -m fruitstory.stage0.run_stage0 b         --video-endpoint <id>      # TTS -> A2V
python -m fruitstory.stage0.run_stage0 c         --video-endpoint <id>      # ID-LoRA
python -m fruitstory.stage0.run_stage0 review                                # fill the verdict column
```
Outputs: `stories/_stage0/{smoke,a,b,c}/`, `jobs.jsonl` per batch (billed seconds), `review.md`. Expected ≈ $2–3 total.
Then write **D7** (primary + fallback voice path) in plan 30 §7 and start stage 1.

## Facts from the research that bind the design (sources in plan 30)

- Open Wan tops out at 2.2 (silent) / S2V (audio in); Wan 2.5–3.0 are API-only. **LTX-2.5** (open, native ComfyUI,
  joint audio) is the primary; LTX-2.3 ID-LoRA the validated voice-reference path; MiniMax H3's license excludes the EU.
- A prompt-only voice is not reproducible across clips → a **locked voice reference per character** (part of the sheet).
- Speech ≈ 2.5 words/s: 5 s ≈ 1 line of ~10 words, 10 s ≈ 2–3 lines. Open joint models are unreliable past one
  speaker; D6 allows 1–3 speakers and stage 0 measures it.
- Stock worker handler returns only `images`-key outputs → core `SaveVideo` for clips; TTS audio is wrapped in a 1-frame mp4.

## Rules in force

Money only on Rida's go (each command is one batch; say the count and ≈ cost first). Every clip reviewed by Rida
before it is used. Code and comments in English; chat in French or English as Rida writes. Nothing in `fruitstory/`
imports `clipping/aistory`.
