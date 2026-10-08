Continue plan 36 (the AI Story rebuilt from scratch in `showrunner/`, Claude-native) — stage 4, the validation run
(episode 1 for real, every gate). `git pull` first.

CONTEXT
- Repo: opensource-clipping-better (rzdhop AI). Plan 36: Claude writes and directs in the chat, `stories/<slug>/` in git
  is the persistence, the `showrunner` MCP connector does GPU jobs, the clip check, the locked voices and the assembly.
  Any universe, not fruit only (D8). Read first, in this order:
  1. `docs/plans/36-CHECKPOINT.md` (top: stage 3 done — the toolbox gaps closed, the skills, the dry run)
  2. `docs/plans/36-rebuild-from-scratch-plan.md` §2.2 (the six steps), §5 stage 4, §7 (D1–D8)
  3. `docs/MCP.md` (the 24 tools) and `.claude/skills/story-*/SKILL.md` (the eight steps, in order) + `showrunner/skills/RULES.md`
  4. `docs/plans/36-stage3-dry-run.md` (the dry-run story and its three watch items, A-227–A-229)
- Done and live (do not reopen): stages 0–2 (D7: LTX-2.5 picture + voice; multi-speaker preferred; locked voices,
  DEC-323; the showrunner server replaced rzdhop-story, DEC-325) and stage 3: every GPU prompt is built by the server
  from the story's files and `comfy_submit(prompt_from=…)` sends only that (DEC-326); a voice is locked from an
  approved casting take with `voice_ref_from_take` (DEC-327, the stage-0 way); `prompts/full_body.md`; the A-222
  three-quarter wording names no other person (keyframes stay `faces_camera` until its check passes); ep00 = the
  casting reel. Plan 36 spent ≈ $2.51. Dry-run story: `stories/les-heritiers-du-fournil` (French, claymation; nothing
  locked, every lock waits for Rida).
- Rida's side before stage 4 (check it is done): host `git pull` + `sudo systemctl restart showrunner-mcp` (the connector
  must list 24 tools); the 8 skill zips uploaded in claude.ai (`python3 showrunner/tools/build_skills.py --zip
  outputs/skills`), story-director / fruit-drama-episode turned off.

DO NOW
1. `git pull`; tests: `python3 -m pytest showrunner/tests -o addopts="" -q` (130 + 3 skipped) and the server's in the
   venv: `PYTHONPATH=<a scratch pytest> .venv/bin/python -m pytest showrunner/tests -o addopts="" -q` (150 + 1 skipped).
   Never pip/uv-sync into `.venv` (the live unit runs from it).
2. Ask Rida: the story of the validation run (the dry-run story — confirm its concept and universe — or a new pitch),
   and his go for the A-222 check (1 keyframe in three-quarter view, ≈ $0.03).
3. Run the story through the skills, step by step, every gate Rida's: universe → cast (sheets, full bodies, turnaround,
   expressions, casting reel + voices) → script → shots + keyframes → clips (check, locked voices, approval) →
   assembly → mp4 in the chat. Log every defect in `stories/<slug>/ep01/defects.md`; fix templates and skills as you
   go (tests green → commit → action-log line → push); a failed gate stops the run.

RULES
- Money only on Rida's explicit go, per batch, with the count and ≈ cost first; every picture and clip reviewed by
  Rida; no stills, no Ken Burns, no edge-tts, no LLM API call from the app — Claude is the writer. Prompts only through
  the prompt tools. Character Heads ≈ 70 words; never name the unwanted in a positive prompt.
- All code and comments in English. Nothing in `showrunner/` imports `clipping` (test). The live app is not touched.
- Plain words in chat; answer format: short problem + root cause; per solution: description, full code, why; a recap
  table (what, where, why, cost).
