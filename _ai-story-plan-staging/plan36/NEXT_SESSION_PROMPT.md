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
- Findings stage 3 must fix FIRST (verified 2026-10-08 against the code; the smoke hid #2 because its driver script
  ran on the host and imported `showrunner.prompts` directly — a chat cannot):
  1. **No tool writes a voice reference.** `vc_clip` needs `02-cast/<char>/voice_ref.wav`; in the smoke it was copied
     by hand. Add one, e.g. `voice_ref_from_take(story, ep, shot, take, speaker)`: the speaker's part of an approved
     take, cut by the clip check's timings (`verify.speaker_parts`), saved and locked as the voice reference.
  2. **No tool builds the prompts.** The proven wording lives in `showrunner/prompts.py` (the batch-a golden test
     pins it) and nothing exposes it to the chat, so a chat would fill templates by hand — the drift the golden
     exists to stop. Add prompt tools that read the story's own files (`01-universe.md` ## Medium, each
     `sheet.md` ## Head / ## Voice (en), `shots.json` lines and place) and render through `prompts.py`:
     e.g. `prompt_clip(story, ep, shot)`, `prompt_keyframe(story, ep, shot)`, `prompt_cast(story, char, kind)`;
     `comfy_submit` should be fed their output. Note: `stories/faille-d-amour/ep01/shots.json` `clip_prompt` holds the
     batch-a prompts AS SENT (old closing sentence, a record): never reuse them as templates — mark or regenerate.
  3. **No template for the cast's first picture.** `turnaround.md`, `emotions.md`, `keyframe.md` all start from
     `full_body.png`, which nothing makes. Add `prompts/full_body.md` (plan §2.2 step 3: Flux 2 Klein t2i, 832×1216,
     neutral grey background, flat even light, the whole figure, only this one character) + its builder + a test.
  4. **A-222 wording still in the code.** `prompts.KEYFRAME_FRAMINGS["three_quarter"]` still says "turned toward
     someone just off-screen beside the camera" — it drew a stray human in the smoke. `faces_camera` stays the
     default; replace the wording with one that names no other person (e.g. "head turned three-quarters to the
     right, eyes looking past the right edge of the frame") and test it once on the GPU on Rida's go (≈ $0.03).
  5. The old account-level skills `story-director` and `fruit-drama-episode` call deleted tools: the new skills
     replace them (Rida turns the old ones off in claude.ai).
- Stage 3 per the plan: one SKILL.md per step — `story-concepts`, `story-universe`, `story-cast`, `story-script`,
  `story-shots`, `story-clips`, `story-assemble`, `story-next-episode` — each with its prompt template, its checklist,
  its gate question, "what to show Rida", and the showrunner tools it calls. Gate: a dry run of steps 1–4 on a new
  pitch with no GPU ($0).

DO NOW
1. `git pull`; tests: `python3 -m pytest showrunner/tests -o addopts="" -q` (82 + 3 skipped) and the server's in the
   venv: `PYTHONPATH=<a scratch pytest> .venv/bin/python -m pytest showrunner/tests -o addopts="" -q` (98 + 1 skipped).
   Never pip/uv-sync into `.venv` (the live unit runs from it).
2. Propose the stage-3 plan: first the toolbox gaps (findings 1–4: voice reference tool, prompt tools,
   full-body template, the three-quarter wording), then the skills (where they live — repo `.claude/skills/` and/or
   the claude.ai account; their order; the dry-run pitch), with open questions; get Rida's approval.
3. Implement step by step: tests green → commit → action-log line → push. Before any GPU job: the count, ≈ cost, Rida's go.

RULES
- Money only on Rida's explicit go; every clip reviewed by Rida; no stills, no Ken Burns, no edge-tts, no LLM API call
  from the app — Claude is the writer. Character descriptions ≈ 70 words; never name the unwanted in a positive prompt.
- All code and comments in English. Nothing in `showrunner/` imports `clipping` (test). The live app is not touched.
- Plain words in chat; answer format: short problem + root cause; per solution: description, full code, why; a recap
  table (what, where, why, cost).
