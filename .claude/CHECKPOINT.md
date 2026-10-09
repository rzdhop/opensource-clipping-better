# CHECKPOINT — where the work stands (replace this file, never append)

**2026-10-09 — plan 36 (AI stories rebuilt as Claude + the showrunner connector): stages 0–3 and 5 done.**

## Live on Rida's host
- `main` pulled; `showrunner-mcp` restarted (21 tools); web app rebuilt (Clips only, no Story pages).
- The `rzdhop-story` skill is on Rida's claude.ai account; the old story skills are gone.
- The claude.ai project "AI gen App" holds only the current context (CLAUDE.md, .claude/*, docs/MCP.md,
  showrunner/README.md); refresh them there after changing them here.
- Old Story data under `outputs/` removed by Rida. Plan 36 GPU spend so far ≈ $2.51.

## Next: the first real episode (plan 36 stage 4)
- In a **fresh claude.ai chat** with the connector and the skill. Rida gives a pitch (e.g. the hacker vs sales
  director fruit drama); the skill proposes, Rida corrects at the six gates; production runs without questions.
- Expected cost ≈ $1–3 (new character ≈ $0.10–0.30; a returning Faille d'amour character $0; ~12 shots
  ≈ $0.60–1.50; each cold start ≈ $0.13–0.15).
- Watch the open story items in `.claude/ASSUMPTIONS.md` (A-222, A-228, A-229, A-221, A-225, A-226). Log every defect
  in `stories/<slug>/ep01/defects.md`; fix the skill (PROMPTS.md, steps) or the server as you go; tests green →
  commit → action-log line → `build_skills.py --zip` for Rida when the skill changed.

## After that (plan 36 stage 6)
- Episode 2 and series memory: `memory.md` from episode 1 (what happened, relationships, open threads, the end
  frame), Rida's audience feedback, the next-episode step proposes three directions; continuity from last frames.
