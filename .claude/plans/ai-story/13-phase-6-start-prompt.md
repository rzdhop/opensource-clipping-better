# Phase 6 — prompt for the starting session (drafted 2026-09-30, at the phase-5 close)

Drafted at the end of the phase-5 close session for the human to paste into a new session. Any decision it
implies counts only when the human sends it.

```text
Start AI Story phase 6 under the repo protocol (FULL task), on this Ubuntu VPS. Work in a new worktree
.claude/worktrees/ai-story-phase-6 on a branch feat/ai-story-phase-6 from main; never switch the main checkout's
branch (the rzc-backend container runs it through a bind mount).

Load first: .claude/CHECKPOINT.md top section (phase 5 DONE, its close-out and follow-ups),
.claude/plans/ai-story/07-phase-6-video-tiers-local.md (the phase-6 brief), .claude/plans/ai-story/00-MASTER-SPEC.md
(the phase-6 parts), .claude/VISION.md "Next", .claude/DECISIONS.md tail from DEC-168, .claude/ASSUMPTIONS.md
Unconfirmed (A-084…A-095 are phase 5's), .claude/claude-action.log tail.
Ids: phase 6 takes DEC-200+ and A-100+ (DEC-195…199 / A-096…099 stay free for late phase-5 notes).
Test policy: DEC-176 (Tier-1 once per stage, -n 4, both envs, by the orchestrator; agents run targeted tests only)
and DEC-192 (essential tests only: one fail-first test per fix plus the working-path guard; live checks walk the
main path).

Scope to plan (EXPLORE → CLARIFY → PLAN, then my approval before any code):
1. Paid estimates end to end, first: book and cap paid LLM calls like media calls (steps/llm_call.py:150-153 does not
   today) — OpenRouter stays unfunded until this ships.
2. Tier 2/3 video: image-to-video shots on the video chain (free/local first, paid only behind allow_paid and the
   caps), the renderer taking a video clip per shot, the partial re-render keeping its guarantees (RC-M8).
3. Local ComfyUI workflows (detected GPU or a reachable host), same chain contract as the hosted links.
4. From phase 5's follow-ups, only what the plan argues for: one image provider per episode (A-087), the voice picker
   avoiding Gemini TTS on the free route (A-091), places pacing (A-092), French scripts running short
   (A-086/A-088/A-089), T1 under-shoot retries, a CLI switch to use the stored Settings, an Edge catalogue check
   (A-093), Hugging Face TTS as a candidate link. The export/import bundle stays a deliberate follow-up unless I say.

Standing rules: NO AUTH on the app, ever (never set API_TOKEN, never show a sign-in; token-on paths by tests only).
allow_paid stays OFF; any paid run needs a shown estimate and my explicit go, with the daily cap as the hard limit
(DEC-194). Deploy only at 0 jobs (Python: sudo docker compose restart backend; dashboard: rm -sfv backend + up -d
--build). Commits use explicit paths and no trailers; never git stash; never revert files with git checkout for
fail-first. Push with GIT_SSH_COMMAND="ssh -i ~/.ssh/github_osc_better -F /dev/null -o IdentitiesOnly=yes".
Keep all stories; deleting any is my call.
```

Session-local helpers the next session may need (not in the repo):
- pytest-xdist: `pip install --no-deps --target ~/.cache/rzc-xdist pytest-xdist==3.8.0 execnet==2.1.2` if the folder
  is gone.
- The CLI inside the container reads keys only from its environment; this VPS keeps them in Settings. Phase 5's
  stage-14 driver loaded the Settings store into the CLI process (paid keys skipped) — see the action log,
  2026-09-30 "the CLI does not see the Settings keys".
