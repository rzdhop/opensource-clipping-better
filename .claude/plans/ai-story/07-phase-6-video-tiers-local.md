# Phase 6 prompt — Tier 2/3 video: local ComfyUI I2V and paid video APIs (opt-in)

Paste this into Claude Code from the repository root. Phase 5 merged and acknowledged.

---

Continue under the repository protocol: read `.claude/VISION.md`, `.claude/DECISIONS.md`,
`.claude/ASSUMPTIONS.md`, `.claude/CHECKPOINT.md`, then
`.claude/plans/ai-story/00-MASTER-SPEC.md` in full. Follow spec section 0.

## Goal of this phase

Turn keyframes into animated shots when the user chooses `tier ≥ 2` for a story. **Keyframe
first, always**: the shot image produced in phase 4 is the first frame; video models are
never asked to invent the character from text (spec 8, research pattern).

1. **VIDEO_CHAIN adapters** (spec 8.1) in `clipping/providers/video.py`:
   `local/comfyui` (workflows `i2v_wan22_5b.json`, `i2v_wan22_14b_lightning.json`,
   `i2v_ltx2.json`, chosen by the hardware profile, spec 8.2/8.3), `fal/seedance-1-pro-fast`,
   `fal/ltx-2-fast` (native audio: **discarded at Tier 2** — dialogue comes from our TTS
   for voice consistency — and kept only under the Tier-3 opt-in of item 3; record the
   DEC with exactly that wording),
   `fal/kling-2.5-turbo-std`, `gemini/veo-3.1-lite`. Same interface as the image adapters;
   `est_cost` per second; polling with the existing time-budget discipline; no silent
   fallback between links — the hop is printed and the shot is marked `video: failed`
   with the reason, the render then uses Tier-1 motion for that shot **only if the user
   ticks "fill failed shots with motion"** (default off).
2. **Video prompt**: `video_prompt` = action sentence + style `motion_rules.tier2_prompt_suffix`
   + camera motion phrase (closed list → phrase table, tested); negative prompt from the
   template; duration = shot `duration_s` rounded to the model's supported lengths
   (table per model), the clip is trimmed/held to the exact `duration_s` at render.
3. **Tier 3** (dialogue-capable models: Veo 3.1 with audio, LTX-2 with audio): exposed as
   `tier: 3` with the same pipeline but `keep_native_audio: true` per shot as an opt-in
   experiment; subtitles still come from our line texts and TTS timing (mark A-entry:
   native lip-sync vs our TTS mismatch is expected).
4. **Route + budget**: per-story `generation_profile.route` (`auto|local|api`); auto =
   local ComfyUI when the profile supports the chosen workflow, else paid link if
   `allow_paid` and caps allow, else refuse with the estimate. The estimate for `assets`
   now includes seconds × price; the ledger records seconds. **`one_dollar` planner**
   (spec 8.5): given the per-episode cap and the image cost already committed, rank shots
   (hook → cliffhanger → peak reveals → longest dialogue shots), animate as many as fit,
   list them for approval with the split; pure function, unit-tested on fixture
   storyboards and price tables.
5. **ComfyUI workflow templates** (spec 8.3): API-format JSON with placeholders for image
   path, prompt, negative, seed, frames, fps, resolution; `object_info` validation before
   queueing (missing nodes/models → explicit install message with model file names);
   `/free` between image and video phases on ≤ 12 GB profiles; progress via `/ws` → job
   activity → SSE.
6. **Renderer**: Tier ≥ 2 inputs (already handled as scaled/padded/trimmed `.mp4` in
   phase 4) get a golden test with a tiny synthetic clip; mixed episodes (some shots
   video, some motion) are allowed and labelled per shot in the manifest.
7. **Dashboard**: the story-level tier (`generation_profile.tier`, editable in the story
   page, with the estimate for the next episode per route) and the per-shot override
   `keep_still` (spec 2.8) — no per-episode tier field; per-shot "animate" / "re-animate
   with note" (`shot:<ep>:<shid>:video`) / "keep still", route badge, generation ETA for
   local (from measured history in `data/usage.json`), cost for API.
8. **CLI**: `--ai-story step <id> assets --ep N --tier 2 --route local|api|auto`.
9. **Docs + decisions**: `docs/AI_STORY.md` (tiers, local setup guide per profile with
   the workflow files and model downloads), DEC "keyframe-first I2V", DEC "native model
   audio discarded in v1", pricing table update with `PRICES_AS_OF`, A-entries with measured
   generation times per model/profile.

## Out of scope

LoRA training per character (document as a future extension with the fal/local cost
numbers), 16:9/1:1, reference-video import.

## Before you plan

Ask only what remains open (for example which GPU is available for the local Tier-2 run).
Propose stages (riskiest: ComfyUI workflow execution and polling), regression contract
(phases 0–5 incl. Tier-1 golden render and clip mode), expected DEC/A, and the Tier-2
script: episode 1 of an existing story re-run at tier 2 on local ComfyUI (Wan 2.2 5B or the
profile's workflow) for 3 shots and kept-still for the rest; then with `allow_paid` on and
a $1.50 per-story cap, one API shot on the cheapest link, and a refusal when the estimate
for the full episode exceeds the cap; watch the mixed episode on the phone.

## Acceptance

- Tier-1 green in both envs; new tests shown failing pre-change (video prompt builder,
  duration rounding table, route decision, estimate incl. seconds, workflow placeholder
  injection and object_info validation, failed-shot policy).
- A mixed Tier-1/Tier-2 episode renders and the manifest labels each shot; the ledger
  shows seconds and cost; refusal messages carry the numbers.
