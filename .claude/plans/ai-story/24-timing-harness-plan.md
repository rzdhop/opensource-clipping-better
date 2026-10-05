# Plan 24 — the timing harness: every line written to a slot it cannot leave

## Context (2026-10-05, from the human's screenshot of e7412a3efcc6 "Dragon Fruit & Sales Queen")

Episode 1 (narrated_drama_60s_v2, French, native speech, written by gemini-3.8-flash, writing v3) shows 10 timing
warnings: every body scene is over its slot (s02 +2.1 s, s03 +1.9 s, s01/s04/s05 by 0.1–0.5 s), 67.1 s in total. The
log shows no rejection and no retry: every reply was accepted first time. The human: scripts are never consistent or
in time; define constraints for every shot and make the writer produce exactly within them.

**Diagnosis (the exploration of 2026-10-05, file:line in the agent's map):**
1. **Nothing enforces the budget.** `validate_e2_v3` (prompts.py:5382) accepts 0.5×…1.5× of the scene's word budget
   (s02: 13–39 words for a 26-word budget); `write_body_scene` (script.py:869-898) accepts a second attempt "despite
   its word count" (DEC-143: "a word count never fails a scene"); E3 (hook, cliffhanger) has no total-words check;
   J1/E4/repair never see `scene_over`.
2. **Two clocks.** The warning uses chars × 0.070 s (timing.py:35, DEC-127); the v3 budget uses 2.4 words/s
   (timing.py:372). Written French averages 6.6 chars/word, not the 5.7 the budget assumes, so a reply that obeys the
   budget still runs 12–17 % long (s04/s05: inside their budgets, still flagged).
3. **The budget forgets pauses.** `word_budget_v3` (timing.py:393) pays pre-roll and tail only, not the 0.25 s gap
   between lines nor the dissolve-raised tail floor (~0.41 s), and reads `target_duration_s` rather than the slot.
   The 6 s hook's 12-word budget is infeasible by construction (5.24 s of speech holds ~11 words).
4. **The writer is never told seconds.** E2v3 gets "w_lo to w_hi words" and a per-line cap (17 native / 22 narrator)
   that can exceed the whole scene's budget (prompts.py:5261-5276, 4962).
5. **Downstream, the slot bends to the line**: scenes run long, shots plan on the actual length, render holds or
   slows ≤ 1.25× (DEC-250/208). The episode window is the only hard gate (gates.py:75).

## Decisions proposed (the human confirms or changes at approval)

- **D-1 One clock.** `timing.seconds_for(text, lang, provider)` = chars × rate per language × the provider overrun
  (DEC-250's Gemini 1.35; Edge 1.0; measured per-voice rate when the story has one). The warning, the budget and the
  storyboard all call it. The French chars/word used to turn seconds into words becomes the measured 6.6 (v3 budgets
  only; v1/v2 untouched).
- **D-2 A line plan before the writer.** `timing.scene_plan(story, scene)` splits the scene's slot high end into
  per-line slots (narrator / character, from the template's line counts and narrator share), pays every pause
  (pre-roll, gaps, dissolve-aware tail floor) and a 5 % margin, and converts each line slot to a hard word cap with
  the one clock. On native-speech stories each character line slot snaps DOWN to a clip length (4/6/8 s → 7/12/17
  words, `native_speech.capacity`) and the narrator clip to the silent clip length, so the sum of clips ≤ the slot.
  The plan is stored on the scene (`slot_s`, `line_plan`) and the storyboard reuses it (the shots are the plan).
- **D-3 The writer is told seconds and hard caps.** "This scene lasts at most 13 s. Line 1 (narrator): at most 11
  words. Line 2 (Rida): at most 12 words. Hard limits — a longer line is refused." Scene total = the sum of caps.
- **D-4 Hard validation, a trim pass, then failure.** `validate_e2_v3`/`validate_e3_v3`: per-line cap and scene cap
  are hard maxima (the floor stays 0.5× for the fill pass to handle). The retry prompt names the line, its words and
  its cap. "Accept despite the word count" survives only for UNDER-length replies. After the ladder, a bounded trim
  pass (`TRIM_CALLS_MAX` 4 per episode) rewrites ONLY the offending lines ("≤ N words, same meaning, same speaker");
  still over → the step fails with one plain sentence naming the scene (no silent acceptance).
- **D-5 Compliance has no tolerance, because the plan already paid the pauses and the margin:** a reply inside its
  caps never flags; the flag text keeps "Scene sNN is X s over its slot" for legacy scripts.
- **D-6 Narrator share and 2–4 character lines per episode become a plan-level constraint** (E1 assigns which scenes
  carry a character line; E2 is told "no character line in this scene" where none is planned). Optional stage.

## Stages (each one commit, worktree off main, DEC-234 selection in both envs; prompt pins re-pinned on purpose)

- **Stage 1 — the one clock and the line plan (timing.py, native_speech.py, schemas.py).** `seconds_for`,
  `scene_plan`, `slot_s`/`line_plan` on the scene (optional keys; legacy scripts untouched), `word_budget_v3` reads
  the plan. Tests: `tests/test_story_timing_plan.py` (a plan at the cap never flags; pauses paid; native snapping;
  the hook holds ≤ 11 words; Gemini factor applied), `test_story_timing.py`, `test_story_native_speech_*`. Risk low.
  Rollback: revert; no stored key is required.
- **Stage 2 — the writer told, the validator hard (prompts.py, steps/script.py, steps/llm_call.py).** E2v3/E3v3
  sentences from the plan; hard caps; the retry prompt names the overshoot; E3 total check; under-only acceptance.
  Deliberate re-pins of the E2v3/E3v3 prompt goldens (RC-M1) with a dated comment. Tests: `test_story_prompts_v3.py`
  (the sentence, the hard refusal, the retry text), `test_story_episode_steps.py` (over → retry → accepted only when
  inside; under still accepted), the prompt goldens. **Riskiest stage**: tighter caps can starve the writer (more
  retries, under-length, clipped meaning) — mitigated by the floor staying 0.5×, the fill pass, and the Tier-2 walk.
- **Stage 3 — the trim pass and the failure sentence (steps/script.py, prompts.py).** `TRIM_CALLS_MAX`, the
  trim prompt, the StepFailed sentence; the activity log line. Tests: `test_story_episode_steps.py` (over after the
  ladder → one trim call → inside; still over → the step fails naming the scene; the call cap).
- **Stage 4 — the storyboard reads the plan (steps/storyboard.py, shots.py).** Native shot plan and beat shots take
  `line_plan` when present (clip per line as planned); legacy scripts keep today's path byte-identical. Tests:
  `test_story_native_speech_plan.py`, `test_story_storyboard*.py`, the shots goldens unchanged for legacy fixtures.
- **Stage 5 (optional, D-6) — narrator share and character-line count as plan constraints (prompts E1v3/E2v3,
  schemas).** Tests in `test_story_prompts_v3.py`.
- **Stage 6 — the dashboard "Trim" action (DurationBar.jsx, ScriptPane.jsx, API).** Each `trim_line` flag gets a
  button that calls `POST /regenerate` with target `scene:<ep>:<sid>` and a trim note built from the plan (no new
  route); image rebuild at 0 jobs. Tests: `test_dashboard_*` source pins, `test_stories_api_*` for the note.
- **Stage 7 — docs, DEC-298…, A-166…; the Tier-2 walk:** the human regenerates episode 1 of e7412a3efcc6 and reads
  0 timing warnings; one new story written end to end.

## Rejected alternatives
- Speeding up or cutting audio at render (DEC-250 forbids it; it hides the writing problem).
- Widening the slots to the written lines (the format's rhythm is the product; s06 shows slots are not too small).
- Only tightening the 1.5× ceiling to 1.0× without a plan (keeps two clocks; the hook stays infeasible; the writer
  still never sees seconds).

## DECISIONS check
Touches DEC-127 (the French rate: kept as the base, provider factors added), DEC-143 (amended: a word count over
the cap now fails a scene; under keeps its rule), DEC-250 (the overrun reaches the estimate and the writer; the
render-side slow-down stays as a last resort), DEC-252 (beat shots take the plan when present), DEC-275 (writing v3
extended: budgets become caps), DEC-231 (the episode gate kept; a per-scene gate added upstream), DEC-234/278/297
(test policy). No conflicts; three DECs amended by name.

## Open questions (CLARIFY)
Q1 After the trim pass, still over: FAIL the step (proposed) or accept with the flag?
Q2 Stage 4 (shots follow the plan) now, or after the writer stages prove out?
Q3 Stage 5 (narrator share, 2–4 character lines) in this plan?
Q4 Calibrate the French chars/word to the measured 6.6 for v3 budgets (proposed) — existing scripts' estimates do
   not change (the estimate stays chars × rate); only new budgets shrink.
Q5 Per-provider overrun in the estimate: the Script-step bar of existing Gemini-voiced scripts will read longer
   (truthfully). Accept?
