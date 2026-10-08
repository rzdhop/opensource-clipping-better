# Plan 36 — CHECKPOINT (2026-10-08 12:30, cloud session linked to the Windows PC; merged onto origin/main)

## STAGE 0 DONE — D7 written (2026-10-08 evening, local session)

- **D7 = path (a)**, LTX-2.5 I2V joint picture+voice; multi-speaker clips preferred; (b) and (c) rejected; no fallback path; next cheap test = Chatterbox voice conversion of (a) lines to a locked voice. Plan §7 D7.
- **Spend:** stage 0 ≈ $2.30 (smoke 0.13, images 0.04, a 0.67, b 0.55, c 0.91), the queue never billed.
- **Learned:** cfg 1.0 everywhere (negative ignored → `CLEAN_FRAME`, positives never name the unwanted); I2V keeps the keyframe's pose; the volume's datacenter runs short of GPUs (waits 20–35 min, `wait()` patient); Chatterbox runs on past the line (line guard in `verify.py`).
- **Next action (next session):** follow `_ai-story-plan-staging/plan36/NEXT_SESSION_PROMPT.md` — the stage-1 plan `docs/plans/36-stage1-plan.md` (corrected 2026-10-08 with Rida's remarks: ~70-word characters, the batch-a prompts frozen as a golden in `docs/plans/36-stage0-batch-a-prompts.json`, the three-quarter keyframe a hypothesis) is PROPOSED; its 4 questions (§5) block the start.
- **Tier 1:** `python -m pytest showrunner/tests -q` 29 green. Stage-0 media in `stories/_stage0/` (gitignored).

Resume prompt for the next session: `_ai-story-plan-staging/plan36/NEXT_SESSION_PROMPT.md`.

## What happened at the merge (read this first)

The PC this session was linked to was at plan 29 (2026-10-06). **origin/main was 81 commits ahead**: plans 30–35
were done on the other system on 2026-10-06/07 — `30-keyframes-warn-only`, `31-comfy-tts-chatterbox`,
`32-fruit-drama-product`, `33-mcp-voice-tools-honest-ledger`, 34 and 35 ("the talking ep01 delivered":
`outputs/faille_damour/ep01/faille_damour_ep01_v3.mp4`, 13 Wan 2.2 **S2V** talking close-ups cut to Gemini voice
lines). So this plan was renumbered **30 → 36** and its worker image now **extends the repo's own**
`ghcr.io/rzdhop/worker-comfyui-tts` (`docker/worker-comfyui-tts/`, built by `.github/workflows/worker-tts-image.yml`:
Chatterbox pinned, chatterbox/audio_encoders volume symlinks, a `patch_handler.py` that returns SaveAudio outputs
under `audio`). Also now in the repo and relevant to the rebuild: the MCP server source (`mcp_server/`, 39 tools,
`docs/MCP.md`: `tts_line`, `tts_batch`, `voice_ref_make`, `file_upload`, `comfy_submit`, `cost_ledger`,
`story_make_episode` …), the `s2v_wan22` and `tts_chatterbox` templates, three endpoints (video / image / voice,
env names in `docs/MCP.md`), and plan 32's presets/recipe. The plan-36 thesis stands — Rida's words at the start:
"none of the produced videos were at minimum good" — the S2V ep01 v3 included; stage 0 compares LTX native speech
against it.

## Where we are

- **Plan 36 accepted** (`docs/plans/36-rebuild-from-scratch-plan.md`, D1–D6 in §7; D7 = the voice path, pending stage 0).
- **Stage 0 code COMPLETE, not yet run on a GPU** — `showrunner/` (stdlib + ffmpeg, no import of `clipping/aistory`):
  worker image extending the tts image, four API-format workflows validated against ComfyUI **v0.34.0** source
  (LTX-2.5 I2V joint audio · LTX-2.5 A2V · LTX-2.3 ID-LoRA · Chatterbox line via SaveAudio), the runner and matrix on
  the `productions/faille_damour` cast (committed: chars + keyframes + script; `ep01/out` and `clips` ignored),
  15 offline tests green (`python -m pytest showrunner/tests -q` — they are outside the CI `testpaths`).
- `.claude/` was NOT touched by this session (the PC's `.claude/*` showed line-ending-only diffs; not committed).
  The upstream `.claude/CHECKPOINT.md` is plan 35's; add a plan-36 header there from the next system.

## What the next system must have

1. `RUNPOD_API_KEY` in `.env` (the app's key) + the endpoint ids (`RUNPOD_COMFY_ENDPOINT_ID` video,
   `RUNPOD_IMAGE_ENDPOINT_ID` image, `RUNPOD_AUDIO_ENDPOINT_ID` voice — see `docs/MCP.md`).
2. The LTX weights on the shared volume: `HF_TOKEN=... bash showrunner/worker/fill_volume.sh` from a pod (≈ 100 GB;
   gated `Lightricks/LTX-2.5` and `LTX-2.3-fp8` accepted). Chatterbox weights are already there if the voice endpoint works.
3. The video endpoint on `ghcr.io/rzdhop/showrunner-worker` (build from `showrunner/worker/Dockerfile`; or add a job
   to `worker-tts-image.yml`) — or a fourth endpoint for the spike. Settings: `showrunner/worker/ENDPOINT.md`.
4. The `rzdhop-story` MCP answered **502** from the cloud session all day (`CLIENT_HTTP_NOT_IMPLEMENTED`); the runner
   talks to RunPod directly, so stage 0 does not need it; stage 2 does. The server unit is `deploy/rzdhop-story-mcp.service`.

## Session 2026-10-08 (A1 host, local) — what is ready, what the human must do

Checked for free (no GPU job): `showrunner/tests` 15 → 16 green. Found and fixed: `keyframe3` sent `RUNPOD_API_KEY`
to the image endpoint, which has its own key (403) → `api_key(name=...)` + `RUNPOD_IMAGE_API_KEY`; the smoke command
in ENDPOINT.md; ENDPOINT.md now says a **fourth** endpoint (the live video endpoint runs the app's S2V clips).
Added `.github/workflows/showrunner-worker-image.yml` (builds `ghcr.io/rzdhop/showrunner-worker:0.1.0`).

Renamed the same day: `fruitstory/` → **`showrunner/`**, image `ghcr.io/rzdhop/showrunner-worker:0.1.0`, CI `.github/workflows/showrunner-worker-image.yml` (D8: any universe, not fruit only). The `fruitstory-worker` GHCR package built by `6cd7eb0` is abandoned (the human may delete it). Stage 0 gains one non-fruit character: **Camille** (`cartoon_human` universe: 3D cartoon human, French woman, early 30s, chosen by Rida 2026-10-08); each character now carries its own universe (medium + negative), the 26 fruit prompts byte-identical; `a`/`b`/`c` skip a character until its keyframe is picked. Tests 19 green.

**Update 2026-10-08 (later):** HF token added to `.env`, LTX-2.5 license accepted (preflight ok); the volume filled by Rida from a pod (`/workspace/models`: diffusion_models 21G, text_encoders 24G, checkpoints 28G, latent_upscale_models 1.9G, chatterbox 3.0G — the sizes match `fill_volume.sh`). Existing endpoints kept as they are: `comfy-video` (`runpod/worker-comfyui:5.10.0-base`, the live app's Wan 2.2 / S2V) and `comfy-images` (`worker-comfyui-tts:latest`); stage 0 gets its own `showrunner-video` endpoint. Next: its id → preflight → `smoke` on Rida's go.

**Update 2026-10-08 (ready for smoke):** `showrunner-video` = `8o50u2dipy57br` (L40S, its own key `RUNPOD_SHOWRUNNER_VIDEO_KEY`; both in `.env`, the runner defaults to them). Rida approved: Camille, the Gemini voices (locked: paloma Aoede 6.5 s, marie_jeanne Kore 6.4 s, rida Puck 4.7 s → `stories/_stage0/voices/`), and **~70-word character descriptions** (all four rewritten from the reference images: MJ's blazer is cream and she wears a watch). Preflight: all ok but Camille's keyframe, the three-speaker keyframe and Camille's voice (from a batch-a take). Both endpoints showed 3 throttled workers (GPU shortage in the datacenter).

**Batch a verdicts (Rida, 2026-10-08):** single clips — take **s33** preferred across characters; exchanges — **s22** preferred ("s23" read as s22: exchanges ran s11/s22 only). Camille's voice locked from `camille_fr_s33` (first 4.0 s: 2.4 s of speech, trailing silence cut). Found: burned-in subtitles in 6/16 clips; every workflow samples at cfg 1.0, so the negative prompt is ignored — the positives now end on `CLEAN_FRAME` and never name text/subtitles/captions (test). Keyframes face the camera, so dialogue framing barely took: stage 2 makes keyframes in 3/4 view. Stage 0 spend ≈ $0.84. Next: `b` then `c` on Rida's go.

Still missing (the human's side):
1. **No HF token on this system** (`HF_TOKEN=` is empty in `.env`; an earlier note here wrongly read the anonymous
   check as "license not accepted"). Only `Lightricks/LTX-2.5` is gated; LTX-2.3, LTX-2.3-fp8 and the Comfy-Org
   files download without a token. All 10 files of `fill_volume.sh` exist on HF (≈ 83 GB).
2. ~~The GHCR package must be made public~~ — done by itself: `ghcr.io/rzdhop/showrunner-worker:0.1.0` is public
   (preflight, 2026-10-08).
3. The volume: size and datacenter unknown from here (the `RUNPOD_API_KEY` is restricted: no account REST); needs
   ≈ 85 GB free for the LTX weights.
4. The spike endpoint (ENDPOINT.md settings, same volume) and its id; the restricted key must cover it.
5. `BUCKET_*` on that endpoint: optional (base64 output fits a 5–10 s 704×1280 clip).
6. The `rzdhop-story` MCP did not resolve from the local session either → path (d) S2V via MCP is not possible today.

## Stage 0 run order (from the repo root)

```
python -m pytest showrunner/tests -q
python -m showrunner.stage0.run_stage0 smoke     --video-endpoint <id>
python -m showrunner.stage0.run_stage0 keyframe3 --images-endpoint <id>
python -m showrunner.stage0.run_stage0 keyframe  --character camille --images-endpoint <id>   # 2 candidates (D8)
python -m showrunner.stage0.run_stage0 keyframe  --character camille --pick <seed>        # free, after Rida picks
python -m showrunner.stage0.run_stage0 a         --video-endpoint <id>      # 9 single clips + 2/3-speaker exchanges
python -m showrunner.stage0.run_stage0 review
python -m showrunner.stage0.run_stage0 voice --character paloma --from stories/_stage0/a/paloma_fr_s22.mp4   # x3
python -m showrunner.stage0.run_stage0 b         --video-endpoint <id>      # TTS -> A2V
python -m showrunner.stage0.run_stage0 c         --video-endpoint <id>      # ID-LoRA
python -m showrunner.stage0.run_stage0 review                                # fill the verdict column
```
Option: the 13 Gemini voice WAVs of `outputs/faille_damour/ep01/voices/` (if present on that system) can be the
locked voice references instead of the path-(a) takes (`voice --from <wav>`). Add the live S2V path as (d) with
`s2v_wan22` on the same lines for the comparison. Expected ≈ $2–3 total; then write **D7** in plan 36 §7.

## Facts from the research that bind the design (sources in plan 36)

- Open Wan tops out at 2.2 (silent) / S2V (audio in); Wan 2.5–3.0 are API-only. **LTX-2.5** (open, native ComfyUI,
  joint audio) is the primary candidate; LTX-2.3 ID-LoRA the voice-reference path; MiniMax H3's license excludes the EU.
- A prompt-only voice is not reproducible across clips → a **locked voice reference per character** (part of the sheet).
- Speech ≈ 2.5 words/s: 5 s ≈ 1 line of ~10 words, 10 s ≈ 2–3 lines. Open joint models are unreliable past one
  speaker; D6 allows 1–3 speakers and stage 0 measures it.
- worker-comfyui 5.10.0 = ComfyUI 0.34.0 (enough for LTX-2.5); its stock handler returns `images` only — the repo's patch adds `audio`.

## Rules in force

Money only on Rida's go (each command is one batch; say the count and ≈ cost first). Every clip reviewed by Rida
before it is used. Code and comments in English; chat in French or English as Rida writes. Nothing in `showrunner/`
imports `clipping/aistory`. No stills, no Ken Burns, no edge-tts, no LLM API call from the app.
