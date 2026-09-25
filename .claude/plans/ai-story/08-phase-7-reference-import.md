# Phase 7 prompt — reference-video import: style from video, premise remix, cast archetypes

Paste this into Claude Code from the repository root. Phase 6 merged and acknowledged
(phase 7 only depends on phases 0–2 technically, and can be scheduled earlier if wanted).

---

Continue under the repository protocol: read `.claude/VISION.md`, `.claude/DECISIONS.md`,
`.claude/ASSUMPTIONS.md`, `.claude/CHECKPOINT.md`, then
`.claude/plans/ai-story/00-MASTER-SPEC.md` in full. Follow spec section 0.

## Goal of this phase

Let a user upload a TikTok/Short they admire and reuse three things from it — its **visual
style**, its **premise** (as a remix) and its **cast dynamic** (archetypes) — as described in
spec section 12. Nothing is downloaded from platforms; the user uploads the file, exactly
like clip jobs (a refusal to fetch URLs parks nothing here: imports are upload-only).

1. **Analyzer** `tools/analyze_reference.py` (usable standalone, stdlib + ffmpeg/ffprobe +
   the repo's provider code) and step `import`:
   - `ffprobe` metadata; audio extraction → existing STT chain → transcript (language
     detected with `clipping/analysis/langdetect.py`);
   - scene cuts via `ffmpeg -vf "select='gt(scene,0.3)',showinfo"` → cut list, shot count,
     mean/median shot length, position of the first spoken word, last-cut-to-end gap
     (cliffhanger detection heuristic), on-screen-text presence (sample frames → vision);
   - frame sampling: 1 frame per cut (max 24) → 2 contact sheets (3×4) → `VISION_CHAIN`
     prompt **V1** → style fragments: rendering, palette (hexes), lighting, camera
     habits, character design language, typography of overlays;
   - transcript + frame descriptions → prompt **V2** → premise skeleton, conflict, stakes,
     archetypes (no names, no copyrighted characters — the prompt instructs to
     generalise), tone;
   - output `imports/<id>/analysis.json` (+ frames, sheets), validated by a schema; the
     pacing statistics are stored for information and compared with the A-entries of
     spec 6.2 (print a small table: measured vs assumed).
2. **Reuse actions** (endpoints under `/api/stories/{id}/imports/{iid}/…`):
   - `use-style` → creates a **custom style template** JSON in the story folder
     (`styles/custom_<iid>.json`, `template_id: "custom:<iid>"`, `version: 1`, passing
     the same schema test as shipped templates) by merging V1 fragments into the closest
     shipped template (choose by rendering keywords; user can pick), fully editable, then
     the normal style-lock step;
   - `remix-premise` → the V2 skeleton becomes the seed of prompt **C1** ("same skeleton,
     new world and characters"); the resulting concept is marked `concept_id: "import:<iid>"`;
   - `mirror-cast` → archetypes feed **K1** as `archetype` hints for the cast sketch.
3. **Estimates**: STT minutes + vision calls priced per route; free chains first.
4. **Dashboard**: upload card in `NewStoryWizard` step 1 (and in the story page for later
   imports); analysis card with the palette swatches, style summary, premise skeleton,
   archetype chips, measured pacing table; the three action buttons. Phone layout checked.
5. **CLI**: `python tools/analyze_reference.py <file> [--story <id>]`,
   `--ai-story step <id> import --file <path>`.
6. **Docs + decisions**: `docs/AI_STORY.md` (import), DEC "imports are upload-only and
   extract style/premise/archetypes, never copy characters or text", A-entries resolved
   or invalidated with the measured pacing of the user's reference videos.

## Out of scope

Turning pacing statistics into episode templates (recorded as a possible later feature),
platform downloads, copying dialogue.

## Before you plan

Ask only what remains open. Propose stages (riskiest: vision prompt quality on contact
sheets across free providers), regression contract (phases 0–6, clip ingestion path
untouched), expected DEC/A, and the Tier-2 script: upload two of my reference videos,
inspect both analyses on the phone, create a story with `use-style` + `remix-premise` +
`mirror-cast`, run it to a rendered episode 1 through fast-track on the free route, and
compare the measured pacing table with the assumptions in `ASSUMPTIONS.md`.

## Acceptance

- Tier-1 green in both envs; new tests shown failing pre-change (cut-list parser from
  `showinfo` fixture text, contact-sheet layout math, V1/V2 golden strings, template
  merge, analysis schema, upload-only guard).
- `analysis.json` validates; a custom style template produced from a video passes the
  style-template schema test; the remixed concept carries `import:<iid>`.
