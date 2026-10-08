Continue plan 30 (the AI Story rebuilt from scratch, Claude-native) — stage 0, the voice-path spike.

CONTEXT
- Repo: opensource-clipping-better (rzdhop AI). The old AI Story (`clipping/aistory`, 75k lines, 29 plans) never produced a good episode; plan 30 rebuilds it with Claude as writer/director in the session, the repo `stories/` folder as persistence, and an MCP server that only does GPU jobs (RunPod ComfyUI), assembly and verification. Read first, in this order:
  1. `_ai-story-plan-staging/plan30/30-CHECKPOINT.md` (where we are, run order)
  2. `_ai-story-plan-staging/plan30/30-rebuild-from-scratch-plan.md` (the plan; decisions D1–D6 in §7, D7 pending)
  3. `fruitstory/README.md` and `fruitstory/worker/ENDPOINT.md`
- Decisions already taken (do not reopen): stage 0 tests all three voice paths and picks after listening (D1); video endpoint = L40S 48 GB, LTX-2.5 int8 (D2); persistence = `stories/` in git (D3); episodes in FR or EN per story (D4); no story dashboard, chat only (D5); 1–3 speakers per clip allowed, stage 0 measures it (D6).
- Binding facts from the research: open Wan stops at 2.2 (silent) — Wan 2.5/2.6/2.7/"3.0" are API-only; LTX-2.5 is the native-speech model, LTX-2.3 ID-LoRA the voice-reference path, MiniMax H3's license excludes the EU; a prompt-only voice does not repeat across clips, so each character gets a locked voice reference; speech ≈ 2.5 words/s (5 s = 1 line, 10 s = 2–3 lines); the stock worker-comfyui handler returns only `images`-key outputs (core SaveVideo works, VHS does not); the endpoint runs worker-comfyui 5.10.0 = ComfyUI 0.34.0.
- Stage 0 code is COMPLETE and validated offline (`fruitstory/`: worker image, 4 API-format workflows checked against ComfyUI v0.34.0 source, runner + matrix on the `productions/faille_damour` cast, 15 tests). Nothing has run on a GPU yet. The `rzdhop-story` MCP answered 502 last session; it is not needed for stage 0.

THIS SYSTEM HAS: `RUNPOD_API_KEY` in `.env` and the keys in the app settings.

DO NOW
1. `python -m pytest fruitstory/tests -q` — must be green here.
2. Ask me for: the video endpoint id (if not built yet, walk me through `fruitstory/worker/`: GHCR build, ≥150 GB volume, `fill_volume.sh` with an HF token that accepted Lightricks/LTX-2.5 and LTX-2.3-fp8, endpoint per ENDPOINT.md), the images endpoint id (Flux 2 Klein, existing), and whether R2/S3 env vars are set on the endpoint.
3. Run stage 0 in this order, one batch at a time, telling me the clip count and ≈ cost before each and waiting for my go: `smoke` → `keyframe3` → `a` → `review` → I pick one take per character → `voice` ×3 → `b` → `c` → `review`. Show me the contact sheets and the review table after each `review`; I judge voice, lips, identity, motion per clip.
4. After my verdicts, write D7 (primary + fallback voice path) in plan 30 §7, update the checkpoint, then propose stage 1 (store + skeleton) as a staged plan with questions.

RULES
- Money only on my explicit go; every clip is reviewed by me before it is used; no stills, no Ken Burns, no edge-tts, no LLM API calls from the app — you are the writer.
- All code and comments in English. Nothing in `fruitstory/` imports `clipping/aistory`.
- Answer format: short problem + root cause; per solution: description, full code, why; a recap table (what, where, why, cost).
