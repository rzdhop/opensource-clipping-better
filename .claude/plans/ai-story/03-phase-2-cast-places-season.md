# Phase 2 prompt — cast (reference sheets + voices), places, props, season arc

Paste this into Claude Code from the repository root. Phase 1 merged and acknowledged.

---

Continue under the repository protocol: read `.claude/VISION.md`, `.claude/DECISIONS.md`,
`.claude/ASSUMPTIONS.md`, `.claude/CHECKPOINT.md`, then
`.claude/plans/ai-story/00-MASTER-SPEC.md` in full. Follow spec section 0.

## Goal of this phase

Workflow steps 5–7 (spec section 3): the consistency assets every episode will depend
on, and the season arc.

1. **Characters** (spec 2.3, 5, 3 step 5):
   - Prompt **K1** (builder + golden test): one character from a cast sketch →
     `descriptor` (≤ 45 words, appearance only), 2–3 `signature_items`, `personality`,
     `speech_style`, `voice.direction`, relationships to the existing cast.
   - `prompt_block` = pure function of descriptor + signature items +
     `style_lock.character_design_rules` (tested). Prompts never use the name.
   - Reference sheets through `IMAGE_CHAIN` / `IMAGE_EDIT_CHAIN`: portrait (t2i, seed
     recorded), turnaround and expressions (edit-with-reference when the route supports
     it — local ComfyUI or a paid editor with `allow_paid`). When no edit link is runnable
     the step **stops and asks**; only after the user sets the story to
     `consistency_mode: prompt_only` (spec 8.1) does it continue with t2i + the same
     `prompt_block` + seed, labelling every such image **"consistency: prompt-only"** in
     the UI and in `character.json`. Never switched automatically (DEC to record). Each
     image regenerable alone with a note; user uploads to
     `refs/uploads/` are extra references (UI text: design references for stylised
     characters; real-person imitation is not supported).
   - **Voices** (spec 11): `voices.json` per TTS provider (`voice_id, lang, gender, age,
     style_tags`), one distinct voice proposed per character in the story language (no
     two leads share a voice), proposed in `TTS_CHAIN` order, 3-second `voice_sample.mp3`,
     picker with play in the UI, `rate/pitch/direction` stored. A pinned voice never
     falls back to another provider silently (spec 8.1): a failing provider fails the
     sample and offers an alternate voice. Optional story **narrator** voice
     (`story.json.narrator`, default off) configured here when the concept uses one.
   - Approve per character; `cast_approved` when all `role: lead|support` are approved.
2. **Places & props** (spec 2.4, 2.5): prompts **P1**, **R1**; master plate per place
   (master-plate prompt from spec 5), time variants on demand (edit with the plate as
   reference, or t2i labelled prompt-only); one image per prop; `layout_notes` kept for
   continuity; approve each.
3. **Season arc** (spec 2.6): prompts **S1** (skeleton, N episodes, default 8,
   user-editable 3–12) and **S2** (expand one entry); `series_memory` initialised empty;
   approve → story `ready`.
4. **Estimates**: `GET /estimate/cast|places|season` returns images × unit price per
   route + TTS chars; the approval button shows it; caps enforced (spec 8.5); every call
   appended to the story-level `cost_ledger.json` (spec 2.11) with `ep: null`.
5. **Endpoints** (spec 9.2): `/steps/cast|places|season`,
   `/approve/character:<cid>|place:<pid>|prop:<pid>|season` (→ `cast_approved`,
   `places_approved`), `/regenerate` with the targets of the spec 9.2 grammar for
   characters, places, props and `season:<ep>`;
   `POST /characters/{cid}/uploads` for reference images (reuse the existing upload
   handling and its size limits; unique file names — the known non-unique-upload-name
   follow-up must not be reintroduced).
6. **Dashboard** (spec 10): `CastEditor`, `PlacesProps`, `SeasonBoard` (arc timeline +
   empty series-memory panel); wizard steps 5–7 wired; phone layout checked.
7. **CLI**: `--ai-story step <id> cast|places|season [--auto-approve]`.
8. **Docs + decisions**: `docs/AI_STORY.md` steps 5–7, DEC for "prompt-only consistency
   is a labelled degraded mode", DEC for "no face-identity adapters", A-entries for
   provider behaviours you observe (e.g. whether the Gemini editor honours seeds).

## Out of scope

Episode scripts, storyboard, rendering, imports, new characters mid-season (phase 5).

## Before you plan

Ask only what remains open. Propose stages (riskiest: reference-image editing across
routes), regression contract (phase 0–1 behaviour, clip mode), expected DEC/A, and the
Tier-2 script: FR story from phase 1, cast of 3 (one with an uploaded reference image),
sheets generated on the free route (or local ComfyUI if present), voices assigned and
played on the phone, two places with one night variant, one prop, an 8-episode arc
approved; verify the ledger totals and that a paid route is refused with `allow_paid` off.

## Acceptance

- Tier-1 green in both envs; new tests shown failing pre-change (prompt_block, K1/P1/R1/
  S1/S2 golden strings, voice uniqueness, estimate math, ledger append, approval gating).
- A character folder contains `character.json` + 3 refs + `voice_sample.mp3`; the
  consistency label is correct for the route used.
- Story reaches `ready` only when cast, places and season are approved.
