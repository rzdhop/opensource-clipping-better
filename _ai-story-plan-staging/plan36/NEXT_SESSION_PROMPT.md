Continue plan 36 (the AI Story rebuilt from scratch, Claude-native) — stage 0, the voice-path spike. `git pull` first: everything is on origin/main (commit "plan 36").

CONTEXT
- Repo: opensource-clipping-better (rzdhop AI). The old AI Story (`clipping/aistory`, 75k lines, plans 0–35) never produced an episode I'd post — the S2V "talking" ep01 v3 of plan 35 included. Plan 36 rebuilds it with Claude as writer/director in the session, `stories/` in git as the persistence, and the MCP server limited to GPU jobs (RunPod ComfyUI), assembly and verification. Read first, in this order:
  1. `docs/plans/36-CHECKPOINT.md` (where we are, what the merge revealed, run order)
  2. `docs/plans/36-rebuild-from-scratch-plan.md` (the plan; D1–D6 in §7, D7 pending)
  3. `showrunner/README.md`, `showrunner/worker/ENDPOINT.md`, and `docs/MCP.md` for the existing endpoints/tools
- Decisions already taken (do not reopen): stage 0 tests the voice paths and picks after listening (D1); video endpoint = L40S 48 GB, LTX-2.5 int8 (D2); persistence = `stories/` in git (D3); episodes in FR or EN per story (D4); no story dashboard, chat only (D5); 1–3 speakers per clip allowed, stage 0 measures it (D6).
- Binding facts from the research: open Wan stops at 2.2 (silent) / S2V (audio in) — Wan 2.5/2.6/2.7/"3.0" are API-only; LTX-2.5 is the native-speech candidate, LTX-2.3 ID-LoRA the voice-reference path, MiniMax H3's license excludes the EU; a prompt-only voice does not repeat across clips, so each character gets a locked voice reference; speech ≈ 2.5 words/s (5 s = 1 line, 10 s = 2–3 lines); the endpoint runs worker-comfyui 5.10.0 = ComfyUI 0.34.0; our image extends the repo's `worker-comfyui-tts` (its patched handler returns SaveAudio outputs).
- Stage 0 code is COMPLETE and validated offline (`showrunner/`: worker image, 4 API-format workflows checked against ComfyUI v0.34.0 source, runner + matrix on the `productions/faille_damour` cast, 15 tests). Nothing has run on a GPU yet. The `rzdhop-story` MCP answered 502 from the cloud session; stage 0 does not need it (the runner talks to RunPod directly).

THIS SYSTEM HAS: `RUNPOD_API_KEY` and the endpoint ids in `.env` / the app settings (`docs/MCP.md` names them).

DO NOW
1. `git pull`, then `python -m pytest showrunner/tests -q` — must be green here.
2. Check with me: is the LTX-2.5 / 2.3 stack on the shared volume (`showrunner/worker/fill_volume.sh`, gated HF token)? Is a video endpoint running `ghcr.io/rzdhop/showrunner-worker` (build from `showrunner/worker/Dockerfile`, or a CI job next to `worker-tts-image.yml`)? Which endpoint ids for video and image? Are the S3/R2 env vars set? Walk me through what is missing before any spend.
3. Run stage 0 in this order, one batch at a time, telling me the clip count and ≈ cost before each and waiting for my go: `smoke` → `keyframe3` → `a` → `review` → I pick one take per character (or we lock the Gemini WAVs of `outputs/faille_damour/ep01/voices/` as references) → `voice` ×3 → `b` → `c` → `review`. If the MCP is reachable, add path (d): the live `s2v_wan22` on the same lines, for the comparison. Show me the contact sheets and the review table after each `review`; I judge voice, lips, identity, motion per clip.
4. After my verdicts, write D7 (primary + fallback voice path) in plan 36 §7, add a plan-36 header to `.claude/CHECKPOINT.md`, then propose stage 1 (store + skeleton) as a staged plan with questions.

RULES
- Money only on my explicit go; every clip is reviewed by me before it is used; no stills, no Ken Burns, no edge-tts, no LLM API calls from the app — you are the writer.
- All code and comments in English. Nothing in `showrunner/` imports `clipping/aistory`.
- Answer format: short problem + root cause; per solution: description, full code, why; a recap table (what, where, why, cost).
