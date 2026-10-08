Continue plan 36 (the AI Story rebuilt from scratch in `showrunner/`, Claude-native) — stage 1, the story store and the toolbox. `git pull` first.

CONTEXT
- Repo: opensource-clipping-better (rzdhop AI). Plan 36 rebuilds the AI Story with Claude as writer/director in the session, `stories/` in git as the persistence, and the MCP limited to GPU jobs, assembly and verification. Any universe, not fruit only (D8). Read first, in this order:
  1. `docs/plans/36-CHECKPOINT.md` (top section: stage 0 done, D7)
  2. `docs/plans/36-stage1-plan.md` (THE plan for this session: stages 1.0–1.6, checks, 4 blocking questions)
  3. `docs/plans/36-rebuild-from-scratch-plan.md` §2.1 (layout), §2.2 (the six steps), §7 (D1–D8)
  4. `docs/plans/36-stage0-batch-a-prompts.json` (the prompts Rida liked — the golden for the dialogue template)
- Decisions taken (do not reopen): D1–D6; D7 = path (a), LTX-2.5 I2V makes picture and voice from the prompt, multi-speaker clips (2–3 per 10 s clip) preferred, paths b and c rejected, no fallback path; D8 = package `showrunner/`, any universe, the art agreed in chat first.
- Rida's remarks that bind stage 1: character descriptions of about 70 words; batch a's look was the best — the dialogue template reproduces its prompts word for word except `CLEAN_FRAME`; characters rarely speak to camera (the three-quarter keyframe is a hypothesis, A-222, not a rule); never name the unwanted in a positive prompt (cfg 1.0 ignores the negative); every clip approved by Rida; no stills.
- Stage 0 spent ≈ $2.30. Stage 1 is $0 except the optional voice-conversion test (≈ $0.05–0.10).

DO NOW
1. `git pull`, then `python -m pytest showrunner/tests -q` — must be green (29).
2. Ask Rida the 4 questions of `36-stage1-plan.md` §5 (STT engine, VC test now or later, videos in git, demo story) and get the plan approved.
3. Implement stage by stage (1.0 if approved → 1.6): tests green → commit → action-log line → next. Before any GPU job: the clip count, ≈ cost, and Rida's go.
4. After 1.6, Rida watches the demo; then update the checkpoint and propose stage 2 (MCP v2).

RULES
- Money only on Rida's explicit go; every clip reviewed by Rida; no stills, no Ken Burns, no edge-tts, no LLM API call from the app — Claude is the writer.
- All code and comments in English. Nothing in `showrunner/` imports `clipping/aistory`. The live app and its endpoints are not touched.
- Plain words in chat (Rida found earlier reports too technical); answer format: short problem + root cause; per solution: description, full code, why; a recap table (what, where, why, cost).
