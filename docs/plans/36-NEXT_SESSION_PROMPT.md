Continue plan 36 (the AI Story rebuilt from scratch in `showrunner/`, Claude-native) — stage 4, the validation run
(episode 1 for real, every gate). `git pull` first.

CONTEXT
- Repo: opensource-clipping-better (rzdhop AI). Plan 36: Claude writes and directs in the chat, `stories/<slug>/` in git
  is the persistence, the `showrunner` MCP connector does GPU jobs, the clip check, the locked voices and the assembly.
  Any universe, not fruit only (D8). Read first, in this order:
  1. `docs/plans/36-CHECKPOINT.md` (top: stage 3 done — the toolbox gaps closed, the skills, the dry run)
  2. `docs/plans/36-rebuild-from-scratch-plan.md` §2.2 (the six steps), §5 stage 4, §7 (D1–D8)
  3. `docs/MCP.md` (the 21 tools) and the skill `.claude/skills/rzdhop-story/` (`SKILL.md`, then `steps/*.md` in
     order, `PROMPTS.md`)
  4. `docs/plans/36-stage3-dry-run.md` (the dry run's three watch items, A-227–A-229; its story was deleted)
- Done and live (do not reopen): stages 0–2 (D7: LTX-2.5 picture + voice; multi-speaker preferred; locked voices,
  DEC-323; the showrunner server replaced rzdhop-story, DEC-325) and stage 3: Claude writes every prompt in the chat, from the
  story's files and the prompt guide (`PROMPTS.md` of the skill), and the server only makes, checks and cuts
  (DEC-328, which reversed a server-side prompt builder Rida rejected); a voice is locked from an
  approved casting take with `voice_ref_from_take` (DEC-327, the stage-0 way); the A-222 three-quarter wording names
  no other person (keyframes stay facing the camera until its check passes); ep00 = the casting reel. One skill,
  `rzdhop-story` (DEC-331). The old AI Story is deleted from the app, which is Clips only (DEC-332). Plan 36 spent
  ≈ $2.51. The approved fruit look and cast: `stories/faille-d-amour`.
- Rida's side before stage 4 (check it is done): host `git pull` + `sudo systemctl restart showrunner-mcp` (the connector
  must list 21 tools) and the web app rebuilt/restarted (its Story pages are gone); the one skill `rzdhop-story`
  uploaded from `python3 showrunner/tools/build_skills.py --zip outputs/skills`, and the old account skills
  `story-director` and `fruit-drama-episode` deleted. Claude proposes, Rida corrects; production runs without
  questions; six gates (DEC-330).

DO NOW
1. `git pull`; tests: `python3 -m pytest showrunner/tests -o addopts="" -q` (74 + 3 skipped) and the server's in the
   venv: `PYTHONPATH=<a scratch pytest> .venv/bin/python -m pytest showrunner/tests -o addopts="" -q` (94 + 1 skipped).
   Never pip/uv-sync into `.venv` (the live unit runs from it).
2. Recommended: the validation run in a fresh claude.ai chat with the connector and the skill (that is how Rida will
   use it). Rida gives the pitch; the skill proposes, he corrects.
3. Run the story through the skill, step by step, every gate Rida's: universe → cast (sheets, full bodies, turnaround,
   expressions, casting reel + voices) → script → shots + keyframes → clips (check, locked voices, approval) →
   assembly → mp4 in the chat. Log every defect in `stories/<slug>/ep01/defects.md`; fix templates and skills as you
   go (tests green → commit → action-log line → push); a failed gate stops the run.

RULES
- Rida's gates: concept, universe, cast sheets, finished cast (pictures + locked voices), script, whole episode; in
  between Claude produces, chooses and locks, saying the cost once (stop past 2× the estimate); no stills, no Ken Burns, no edge-tts, no LLM API call from the app — Claude is the writer and writes every
  prompt; the MCP only makes, checks and cuts. Ask Rida before changing anything about how the system works. Character Heads ≈ 70 words; never name the unwanted in a positive prompt.
- All code and comments in English. Nothing in `showrunner/` imports `clipping` or `web` (test).
- Plain words in chat; answer format: short problem + root cause; per solution: description, full code, why; a recap
  table (what, where, why, cost).
