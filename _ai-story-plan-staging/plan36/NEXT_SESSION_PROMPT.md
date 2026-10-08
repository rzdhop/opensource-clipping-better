Continue plan 36 (the AI Story rebuilt from scratch in `showrunner/`, Claude-native) — stage 3, the skills. `git pull` first.

CONTEXT
- Repo: opensource-clipping-better (rzdhop AI). Plan 36: Claude writes and directs in the chat, `stories/<slug>/` in git
  is the persistence, the `showrunner` MCP connector does GPU jobs, the clip check, the locked voices and the assembly.
  Any universe, not fruit only (D8). Read first, in this order:
  1. `docs/plans/36-CHECKPOINT.md` (top: stages 0–2 done, the replacement, the smoke)
  2. `docs/plans/36-rebuild-from-scratch-plan.md` §2.2 (the six steps), §5 stage 3, §7 (D1–D8)
  3. `docs/MCP.md` (the 20 tools of the showrunner connector) and `showrunner/README.md`
  4. `showrunner/prompts/*.md` (the templates the skills must use; the batch-a golden is pinned by a test)
- Done and live (do not reopen): stage 0 (D7: LTX-2.5 makes picture + voice; multi-speaker clips preferred), stage 1
  (store, prompts, clip check on the CPU, assembly, demo `stories/faille-d-amour`), locked voices approved by Rida
  (DEC-323: every clip's lines converted per speaker to the character's `voice_ref.wav`), stage 2 (the `showrunner`
  MCP server, 20 tools, DEC-324) which REPLACED rzdhop-story (DEC-325: `mcp_server/` deleted; unit `showrunner-mcp` on
  8787 behind `https://main-network-interface.tail01346d.ts.net/mcp`; Rida re-added the connector). Smoke through the
  public URL ran end to end ($0.16, `stories/smoke-2026-10-08`). Plan 36 spent ≈ $2.51. Everything pushed.
- Findings stage 3 must handle:
  1. **No `voice_ref` tool:** a chat cannot bring a character's voice reference into a story. The cast step needs one
     (e.g. `voice_ref_from_take(story, ep, shot, take, speaker)`: the speaker's part of an approved take, cut by the
     clip check, saved and locked as `02-cast/<char>/voice_ref.wav`; or a TTS voice-design route) — a stage-3 tool.
  2. **A-222 invalidated in its wording:** "turned toward someone just off-screen" in a keyframe prompt DREW a human
     at the frame's edge. Keyframes stay `faces_camera`; a three-quarter wording must name no other person.
  3. The old account-level skills `story-director` and `fruit-drama-episode` call deleted tools: the new skills replace
     them (Rida turns the old ones off in claude.ai).
- Stage 3 per the plan: one SKILL.md per step — `story-concepts`, `story-universe`, `story-cast`, `story-script`,
  `story-shots`, `story-clips`, `story-assemble`, `story-next-episode` — each with its prompt template, its checklist,
  its gate question, "what to show Rida", and the showrunner tools it calls. Gate: a dry run of steps 1–4 on a new
  pitch with no GPU ($0).

DO NOW
1. `git pull`; tests: `python3 -m pytest showrunner/tests -o addopts="" -q` (82 + 3 skipped) and the server's in the
   venv: `PYTHONPATH=<a scratch pytest> .venv/bin/python -m pytest showrunner/tests -o addopts="" -q` (98 + 1 skipped).
   Never pip/uv-sync into `.venv` (the live unit runs from it).
2. Propose the stage-3 plan (where the skills live — repo `.claude/skills/` and/or the claude.ai account; the
   `voice_ref` tool; the order of the skills; the dry-run pitch) with its open questions; get Rida's approval.
3. Implement step by step: tests green → commit → action-log line → push. Before any GPU job: the count, ≈ cost, Rida's go.

RULES
- Money only on Rida's explicit go; every clip reviewed by Rida; no stills, no Ken Burns, no edge-tts, no LLM API call
  from the app — Claude is the writer. Character descriptions ≈ 70 words; never name the unwanted in a positive prompt.
- All code and comments in English. Nothing in `showrunner/` imports `clipping` (test). The live app is not touched.
- Plain words in chat; answer format: short problem + root cause; per solution: description, full code, why; a recap
  table (what, where, why, cost).
