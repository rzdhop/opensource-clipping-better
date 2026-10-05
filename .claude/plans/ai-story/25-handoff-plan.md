# Plan 25 — the handoff: every shot shows its prompts, its provider, its mode (auto or my own) and its upload

## Context (2026-10-05)
The human: "rework the UI/UX to ease the show of prompts for the manual generation of shots or clips video — handoff
prompts to paste into the providers, select mode and propose the auto generation or manual upload." Mapped (exploration
of 2026-10-05, the agent's map + the live screens at phone width):
- The only handoff is `ShotListPane.jsx`: clips only, shown only for `native_speech_manual`, every shot fully expanded,
  one platform `<select>` (Flow / Higgsfield, localStorage), "Copy prompt" per shot, per-thumbnail downloads, a
  whole-episode zip, the last tab on a phone. Keyframe prompts: a read-only accordion in `ShotCard.jsx`, no copy. Sheet,
  plate and prop prompts: nowhere (`fetchImageBrief` has no caller; the tiles say "make it from the image brief").
- The auto/manual choice is per story, hidden as "Budget profile" in a collapsed block; "Mode" means Studio vs Agent.
  Per shot, `clips.class_link` + `upload_target_refusal` decide; a mix of generated and uploaded shots is impossible
  (only stock cutaways, DEC-295).
- **A prompt defect found on e7412a3efcc6:** for characters without a species noun (the human universes of D2),
  `shots.character_handles` yields "the leather loafers", "the", "the in a tailored navy suit"; `character_anchor`
  then builds "the charcoal-ivory female leather loafers character in a charcoal blazer". Every clip prompt of a
  human-cast story reads "The leather loafers grips the pen… looks at the and says…". The handoff would hand over
  broken prompts; this is stage 0.

## Decisions (proposed; the human confirms or changes)
- **D-1 Per-shot mode with a step default.** `assets.json` gains `shot_modes[shot_id] = {"clip": "auto"|"manual",
  "image": "auto"|"manual"}` (optional; absent = the story's profile as today). `clips.class_link`,
  `upload_target_refusal`, the assets step (manual rows await uploads, auto rows go to the profile's API link) and the
  gate (prices only the auto rows) honour it. The step default stays the profile (`budget_profile`, `images`).
- **D-2 One handoff document.** `GET /{id}/episodes/{ep}/handoff?platform=&model=` composes the clip brief, the
  image brief's keyframe entries and the entity entries (sheets, variants, plates, props) into one JSON: per shot
  `{image: {...}, clip: {...}}` each with `mode`, `state`, `prompt` (platform-formatted, hash unchanged — DEC-292),
  `negative_prompt`, `line`, `references[] (+ a per-shot zip url)`, `checks[]`, `upload_slot`, and for auto `{link,
  est_usd, gate: ready|refused + the sentence}`; plus `entities[]`, `counts`, `missing[]`, `next_missing`. The
  Higgsfield model is a query parameter remembered per episode (`assets.json.handoff = {platform, model}`).
- **D-3 A Handoff view, phone first.** Route `/story/:id/episodes/:ep/handoff` (the `#shots` tab and the Agent card
  link there; the old Shot list pane retired): a sticky header (progress "3 of 10 clips · 2 of 10 keyframes", filter
  Missing, "Next missing"), a platform chip row (Flow / Higgsfield + model), and one card per shot, collapsed to its
  head (state chips, length, speaker) and opened one at a time: a segmented **Mode: Auto · My own**; under *My own*:
  the big "Copy prompt", "Copy line", "Copy negative", the references with "Download all for this shot", the checks as
  checkboxes, the Upload button with progress and the refusal sentence inline; under *Auto*: the link, the estimate,
  the gate verdict (`BudgetRefusal` reused) and "Generate this shot". A second section "Images" does the same for
  keyframes, and the Cast / Places / Props tiles gain a "Prompt" drawer (copy + size + reference) fed by the same
  document. The markdown/zip export stays (an "Export" button), gaining an image-brief zip.
- **D-4 The wizard and the profile card say it plainly.** A first-class "How clips are made: Auto (API links) / My
  own (Flow, Higgsfield…)" and "How images are made: Auto / My own" above the details block; the budget profile
  follows (manual ⇒ `native_speech_manual`, `images: manual`); "Mode" (Studio/Agent) keeps its name.
- **D-5 Stage 0 first:** human casts get name-based handles and anchors ("Marie-Jeanne, a woman in her thirties in a
  charcoal blazer"; listeners by name); creature casts byte-identical (the existing goldens prove it).

## Stages (each one commit, worktree off main, DEC-234 selection in both envs; dashboard builds to the scratchpad)
- **Stage 0 — human-cast handles and anchors (shots.py, prompting/clips where the listener is resolved).** Tests:
  `tests/test_story_shots.py` / `test_story_action_prompts.py` (a human cast → names; a fruit cast → byte-identical
  anchors; the listener never empty). Risk low; the e7412a3efcc6 prompts re-read by the human. Opus (prompt goldens).
- **Stage 1 — per-shot mode in the backend (D-1).** `schemas.py`, `clipping/aistory/steps/clips.py` (`class_link`),
  `manual_uploads.py` (`upload_target_refusal`), `steps/assets.py` (rows by mode; the gate prices auto rows),
  `web/api/routes/stories.py` (`PATCH …/shots/{id}/mode`). Tests: a mixed episode (2 manual, 1 auto): the auto row
  is priced and sent to the fake link, the manual rows await uploads, an upload on an auto shot is refused with the
  sentence, flipping a shot to manual stales nothing it has not made. Riskiest stage (money + the assets step). Opus.
- **Stage 2 — the handoff document (D-2).** `steps/brief.py` `handoff()`, the route, the per-shot zip, the image-brief
  zip, `assets.json.handoff`. Tests: `tests/test_api_handoff.py` (shape, states, the gate sentence, the model memory),
  byte-identity of the existing briefs. Sonnet.
- **Stage 3 — the Handoff view (D-3).** `web/dashboard/src/pages/story/episode/HandoffPage.jsx` (+ cards, the mode
  control, copy helpers with the textarea fallback), routes in `App.jsx`, the stepper/Agent-card links, the Shot list
  pane retired. Tests: the dashboard source pins (route lives under /story, RC-M9 no-auth guards, the copy fallback).
  Image rebuild at 0 jobs. Opus (phone-first layout, many states).
- **Stage 4 — tiles with prompts (D-3 second half).** Cast / Places / Props tiles: a "Prompt" drawer from the handoff
  document (copy, size, reference, upload). Sonnet.
- **Stage 5 — the wizard and the profile card (D-4).** `NewStoryWizard.jsx`, `GenerationProfileCard.jsx`, the payload
  mapping. Tests: the wizard source pins. Sonnet.
- **Stage 6 — docs, DEC, the walk:** `docs/AI_STORY.md` "Your own clips" rewritten around the Handoff; the human
  makes 3 shots of e7412a3efcc6 on Flow from the Handoff (time spent, what was missing — plan 22 stage 8's walk).

## Rejected alternatives
- Automating Flow/Higgsfield (their terms; DEC-277). — Only polishing the Shot list in place (clips only; no mode;
  phone unusable). — Dropping the markdown export (the human may still want a file).

## DECISIONS check
Touches DEC-277 (extended: the handoff covers images and a per-shot mode), DEC-292 (kept: formatting stays outside the
hash), DEC-294 (kept: a platform that cannot make the frame is refused), DEC-295 (stock cutaways stay the other mixed
case), DEC-299 (variant tiles gain the prompt), DEC-173 (open app: the new route has no more power than the uploads).
The riskiest stage: stage 1 (per-shot mode through the assets step and the gate).

## Open questions
Q1 Per-shot mixing (stage 1) in this plan, or step-level only (cheaper; no backend mode)? Proposed: per shot.
Q2 The Handoff replaces the Shot list (proposed) or sits beside it?
Q3 Stage 0 now (the broken human-cast prompts), before the UX? Proposed: yes, first.
