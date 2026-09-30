# Phase 5 close — prompt for the next session (drafted 2026-09-30)

Drafted at the end of the stage-13 session for the human to paste into a new session. The three decision lines
(episode 2 acknowledged, fix F4 now, the capped 14b(e) go) are proposals: they count only when the human sends
them in the new session's own message, possibly edited.

```text
Close AI Story phase 5 under the repo protocol (FULL task, resume), on this Ubuntu VPS. Work in the existing worktree
.claude/worktrees/ai-story-phase-5 (branch feat/ai-story-phase-5); never switch the main checkout's branch.

Load first: .claude/CHECKPOINT.md top section (phase 5 in progress: stages 0–12 done, stage 13 walk done incl. the
fixes F3/F7/F9 and the open findings), .claude/plans/ai-story/11-phase-5-plan.md (stages 14, 14b, 15),
.claude/DECISIONS.md tail from DEC-173, .claude/ASSUMPTIONS.md Unconfirmed, .claude/claude-action.log tail.
Next free ids: DEC-177 and A-084 (DEC-176 = the test policy). Test policy DEC-176: agents run targeted tests only;
I run Tier-1 once per stage, in parallel (-n 4), in both envs, plus compileall (and vite build if the dashboard changed).

My answers:
- Episode 2: watched and acknowledged. Stage 13 is closed.
- Fix T2-P5-F4 now: the cast step paces free-tier image limits like the assets step (DEC-168's paced rounds,
  never a paid fallback), so an accepted proposed character completes in one press.
- 14b(e): go, hard cap $0.10, on the throwaway T2 story ab8fc500173e (its assets are paid fal ones). Scope: one
  text-only line edit + re-voice ($0), one shot image regenerate on fal (show the estimate in the log first; stop if
  it is above $0.10), approve, dry run, rerender ($0); the manifest's "N of M" must equal the dry run. Turn
  allow_paid on only for that run (caps 0.10/0.10/0.10) and restore allow_paid off + caps 1/3/10 right after.
  Book the spend in the ledger and compare with fal's price table. No OpenRouter, ever.

Do, in order, one stage at a time (green → commit → log line → ledger row):
1. Stage 13b — polish round [Sonnet]: F4 (above); F1 keep the Season step open after a series job finishes;
   F2 the paste counter counts code points like the server; F5 "Approve proposals" shows approved once the
   propose-next job is completed; F8 "Re-voice this line" (and other regenerate controls needing the approved
   script) disabled with the server's reason. Fail-first tests; my browser check at 375 px on a scratch copy.
   F6 (E4 flash-lite payoff variance) gets no code: an A-entry with the live evidence.
2. Deploy at 0 jobs (dashboard changed: sudo docker compose rm -sfv backend && sudo docker compose up -d --build backend).
3. Stage 14 — one short episode per remaining style (anime, cinematic_real, cartoon_flat, storybook_watercolor,
   claymation), free route, unattended: per style a new story from its library concept, cast of 2, 1 place,
   prompt-only, steps 1–7 via the CLI with --auto-approve, then the fast track with an automatic Continue ≥ 75 s
   after each free-tier stop. Run them back to back in the background; checkpoint after each style. Per style
   record length, loudness, subtitles/font (the new fonts), motion/pan_pct, overlays, what drifted or held → one
   A-entry each. Template fixes only if clearly broken (version bump, named re-pin). I will watch the claymation one.
4. Stage 14b(e) as above.
5. Stage 15 — docs and decisions [Sonnet]: docs/AI_STORY.md (series memory, feedback, proposals, re-edit, partial
   re-render, whole-frame timing, fonts, CLI), VISION "Where it stands" (phase 5 done; next phase 6 video), DEC-177…
   (the plan's list in order + the live fixes F3/F7/F9 + the no-sign-out fix), A-084… (styles, bench 12/12, E4
   variance, Gemini TTS reads notes aloud, pollinations pacing), the export/import bundle as a deliberate follow-up.
6. Close: Tier-1 once on the final tree; ff main; deploy at 0 jobs; push the branch then main with
   GIT_SSH_COMMAND="ssh -i ~/.ssh/github_osc_better -F /dev/null -o IdentitiesOnly=yes"; CI green; CHECKPOINT
   close-out per artifact; then tell me phase 5 is closed and give me the phase-6 starting prompt.

Standing rules: NO AUTH on the app, ever (never set API_TOKEN, never show a sign-in, prove token-on paths by tests
only). allow_paid stays OFF except the capped 14b(e) run. Deploy only at 0 jobs. Commits use explicit paths and no
trailers. Never git stash; never revert files with git checkout for fail-first. Throwaway stories 999b08623375 and
ab8fc500173e are kept. The live FR story b1104ec66b05 is backed up in /home/ubuntu/backups/ai-story-phase-5/.
```

Session-local helpers the next session may need to recreate (they are not in the repo):
- pytest-xdist: `pip install --no-deps --target ~/.cache/rzc-xdist pytest-xdist==3.8.0 execnet==2.1.2` if the folder
  is gone (PEP 668 refuses a user-site install; never force it).
- Scratch preview servers `phase5-throwaway` (:8015) and `phase5-dashboard` (:5177) are in the local, git-excluded
  `.claude/launch.json`; they run on the worktree's own `outputs/` copy, never the live data.
