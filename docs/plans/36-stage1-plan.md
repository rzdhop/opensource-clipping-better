# Plan 36 — Stage 1: the story store and the toolbox (proposed 2026-10-08, to approve next session)

Status: **PROPOSED, not approved.** Four questions (§5) block the start; ask them first.
Context to read before this file: `docs/plans/36-CHECKPOINT.md` (top section), `docs/plans/36-rebuild-from-scratch-plan.md`
§2.1 (layout), §2.2 (the six steps), §7 (D1–D8, D7 = the voice path).

## 1. Goal

Everything a story needs to exist as files and to be assembled into an episode, offline, before stage 2 plugs it into
the chat (MCP v2). No GPU, $0. Nothing in `showrunner/` imports `clipping/aistory`; the live app and its endpoints are
not touched.

## 2. What stage 1 must carry from Rida's remarks (2026-10-08)

| Remark | Where it lands in stage 1 |
|---|---|
| Any universe, not fruit only (D8, DEC-319) | the universe block is a story file (`01-universe.md`); no medium or species is hard-wired in code or templates |
| Character descriptions of **about 70 words** | the character-sheet template and a test: 65–80 words per master head prompt (the stage-0 test, generalised) |
| **Batch a was the nicest; later batches worsened it** | the dialogue template reproduces the batch-a prompts **word for word** (`docs/plans/36-stage0-batch-a-prompts.json`, takes s33 single / s22 exchange), with ONE deliberate difference: the closing "No subtitles, no on-screen text, no black frames." becomes `CLEAN_FRAME` (cfg 1.0 ignores the negative; naming subtitles primed captions in 6/16 batch-a clips). A golden test pins it; any other change is a decision, not drift |
| **Multi-speaker clips preferred** (D7) | the dialogue template takes 1–3 speakers, lines in order, one voice description per speaker; the 2- and 3-speaker forms are the batch-a exchange prompts |
| A character rarely speaks to camera | kept as the batch-a wording ("talks to someone just off-screen … never looking into it"). **Hypothesis, not a rule:** a keyframe drawn in three-quarter view in the shot's framing (A-222); the keyframe template offers it as an option, validated at the first real episode (stage 4) |
| Never name the unwanted in a positive prompt | a test over every rendered template: no "subtitle", "caption", "on-screen text" |
| The camera "holds still on the speaker" | in the dialogue template (path c's "push in" closed in, relit and invented a person) |
| Every clip reviewed by Rida; no stills (DEC-317) | the store's lock states: nothing is assembled from a clip that is not `locked` (approved) |

## 3. Stages (each one committable and testable alone; rollback = revert its commit)

| # | Step | Files | Risk | Verification | Regression items at risk |
|---|---|---|---|---|---|
| 1.0 | *(only if Q2 = now)* **Voice-conversion test**: one batch-a clip per character, its audio converted to the character's locked voice with `FL_ChatterboxVC` (same timing), remuxed on the original picture | `showrunner/workflows/vc_chatterbox.json` (+ `_build.py`), `stage0/run_stage0.py vc` | Medium: the node may download its weights at first use (≈ 1 GB into the volume) | ComfyUI v0.34.0 + Fill-ChatterBox f7d7a16 validation; Rida listens: same lips, the locked voice | none (new command) |
| 1.1 | **Story store** `showrunner/store.py`: the §2.1 layout, slugs and ids, lock states `draft → locked` (a locked file is never overwritten without an explicit unlock), a per-story cost ledger (`costs.jsonl`, execution time billed, DEC-316) | `store.py`, tests | Low | tests on temp folders | none |
| 1.2 | **Prompt templates** `showrunner/prompts/*.md` + renderer: universe block, character sheet (≈ 70-word master head prompt), keyframe (option: three-quarter view in the shot's framing), dialogue clip 1–3 speakers (batch-a golden), silent reaction clip | `prompts/`, `prompts.py`, tests; `stage0/matrix.py` reads from them | Medium: quality shows only on the GPU (stage 4) | golden test vs `36-stage0-batch-a-prompts.json` (only `CLEAN_FRAME` differs); 65–80 words; no unwanted words; speaker order | the 29 stage-0 tests |
| 1.3 | **Image workflows** copied into `showrunner/workflows/`: `t2i_flux2_klein`, `edit_flux2_klein_multiref` (no more import of `clipping.providers`), turnaround and emotion grid as multiref-edit prompts (no new model) | `workflows/`, `comfy_templates.py`, tests | Low | `tools/validate_workflows.py` against ComfyUI v0.34.0 | `keyframe`/`keyframe3` commands |
| 1.4 | **Clip check** `verify.py`: speech-to-text of the clip's own audio aligned to its lines → % words heard, last word ends ≥ 0.1 s before the end, speakers in order (multi-speaker) | `verify.py`, tests | Medium: engine install on the ARM host (Q1) | tests on synthetic speech + the real batch-a clips (s33/s22 should pass) | none |
| 1.5 | **Assembly** `assemble.py`: approved clips only, 1080×1920, trimmed after the last word + 0.3 s, ASS subtitles from the check's timings (one colour per speaker), music bed ducked under voices, end card "Partie N+1 demain"; recipe copied (not imported) from `productions/faille_damour/render_ep01.py` and `tools/episode_cut.py` | `assemble.py`, tests | **The riskiest stage**: the most code, ffmpeg 6.1 quirks (drawtext drops accented tails — ASS only) | tests + a real render watched by Rida | none (the old renderer untouched) |
| 1.6 | **Demo story by hand** (Q4): `stories/<demo>/` from stage-0 material — universe, 4 character sheets (≈ 70 words each), the batch-a clips Rida liked as locked takes — assembled into a ≈ 30 s demo | `stories/<demo>/` (text + images in git per Q3) | Low | **Rida watches the demo** | none |

Order: 1.0 (if now) → 1.1 → 1.2 → 1.3 → 1.4 → 1.5 → 1.6. Tier 1 after each: `python -m pytest showrunner/tests -q`;
the full CI suite runs at every push (showrunner tests are outside its `testpaths`, so nothing there moves).

## 4. Checks

- **Rejected alternative:** start with stage 2 (MCP v2) so the chat drives GPU jobs sooner — its tools read and write
  the store, whose layout must exist first; and the MCP was unreachable on 2026-10-08.
- **DECISIONS.md:** DEC-317 (no stills: assembly refuses a shot without a locked clip) · DEC-316 (the ledger bills
  execution time) · DEC-319 (nothing fruit-specific in code) · DEC-320 (D7: path a, multi-speaker preferred) ·
  DEC-318 superseded for `showrunner/` by DEC-320 · checked, no other conflict.
- **Riskiest stage:** 1.5 (assembly).
- **Regression contract:** the live app, its endpoints (`comfy-video`, `comfy-images`) and `clipping/aistory` are not
  touched; the stage-0 runner keeps working (its 29 tests); `showrunner-video` stays the only endpoint stage 1 may use
  (1.0 only).

## 5. Questions for Rida (blocking — ask before 1.0/1.1)

1. **Clip check engine (1.4):** (A, recommended) Whisper on this server's CPU, free, ≈ 10–30 s per clip ·
   (B) on the RunPod GPU, cents per clip, one more model · (C) no automatic check, Rida's eyes only.
2. **Voice-conversion test (1.0):** now, before 1.1 (≈ $0.05–0.10; decides whether a locked voice stays in the cast
   pack), or later in stage 4? Recommended: now.
3. **Videos in git:** text and images in git, clips and finals out (≈ 25–30 MB of video per episode; kept locally +
   a backup)? Recommended: yes.
4. **Demo story (1.6):** reuse the Faille d'amour stage-0 material (free)? Recommended: yes.

## 6. Facts the next session must not re-derive

- Endpoint `showrunner-video` = `RUNPOD_SHOWRUNNER_VIDEO_ENDPOINT_ID`, key `RUNPOD_SHOWRUNNER_VIDEO_KEY` (both in
  `.env`); images endpoint key `RUNPOD_IMAGE_API_KEY`; never the live `RUNPOD_COMFY_ENDPOINT_ID`.
- `python -m showrunner.stage0.run_stage0 preflight` is free and checks HF, GHCR, both endpoints, local files.
- The volume's datacenter is often short of GPUs: 20–35 min queues happened, free; `wait()` is patient (3 h).
- Every workflow samples at cfg 1.0: the negative prompt does nothing.
- Chatterbox talks on past the line up to ≈ 40 s: `verify.line_cut_s` cuts it.
- VC weights: the node loads `models/chatterbox/chatterbox_vc/` (`s3gen.pt`, `conds.pt`); the same two files sit in
  `chatterbox_multilingual/` on the volume, else the node downloads them.
- Stage-0 media (clips, sheets, locked voices) are in `stories/_stage0/` on this host only (gitignored).
