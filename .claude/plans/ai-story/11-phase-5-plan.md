# AI Story phase 5 — series memory, audience steering, per-scene re-edit, remaining styles

Status: DRAFT for approval (2026-09-29). Brief: `.claude/plans/ai-story/06-phase-5-series-reedit-styles.md`.
**Runs after the auth task** (`~/.claude/plans/auth-opt-in-token.md`, which takes DEC-173 / A-080).
Base: `main` at that task's close (phase 4 merged at `b60938e`, CI green). Next free ids: **DEC-174, A-081**, or
the next free ids at stage 0.

New routes follow the auth task's rule: they go on the existing `require_token` routers, which are open when
`API_TOKEN` is unset. Any new media URL comes from `story_media_url`, which returns a plain path when auth is off.

## Context

Phase 4 shipped the MVP: one episode, rendered, with a metadata pack. Phase 5 makes the mode **serial** and
**editable**:
- episode N's approved script feeds a series memory (a recap, open hooks, relationships). The next episode
  opens on the recap and pays off a hook;
- pasted audience feedback and proposed characters/twists can steer the next episode;
- one line or one shot can be changed and re-rendered without rebuilding the rest;
- the five styles never exercised live (`anime`, `cinematic_real`, `cartoon_flat`, `storybook_watercolor`,
  `claymation`) each get one real episode.

The read side of memory already exists: `context.memory_section` feeds E1, E3 and E4; E3 writes the recap scene
`s00`; the episode templates give it a 2.0–3.0 s slot; `episode_common.py:218` refuses episode 2+ without a recap
(DEC-130). What's missing is the write side (S3/F1/N1), the hook-payoff loop, the re-edit re-timing path, and a
partial re-render that honestly rebuilds only the changed shots.

**Human's answers (2026-09-29):**
- (1) Phase 4 has landed, so plan against the merged `main`.
- (2) The export/import bundle is **deferred**, recorded as a deliberate follow-up with no DEC.
- (3) **Ship the OFL fonts** the five styles name.
- (4) The memory step runs on an **approved script**. Memory records the script revision and goes stale if the
  script changes.

**Execution host:** the Ubuntu VPS clone, as for phases 0–4. The deploy and the live Tier-2 walk need it.

## Key findings the plan is built on (maps of 2026-09-29, file:line on `origin/main`)

**Partial re-render is the hard part.** The runner already reuses a shot clip whose key is unchanged: phase 4's
Tier-2 re-rendered with 20/21 shots cached. But there are three problems:
- **Frame rounding.** Shot `frames` come from cumulative rounding over all shot durations (`render/timeline.py`
  `_cumulative_frames`). A line edit that moves a scene by a non-whole number of frames flips ±1 frame on about
  a third of the later shots, which invalidates their keys (simulated: 34%). "Only the changed shots" is
  impossible until shot timing is in whole frames.
- **The edited scene is never re-timed.** A line edit stales its scene (`episode_common.mark_changed`), and
  `shots.retime_storyboard` skips stale scenes. The render then either fails closed ("total disagrees") or,
  when the total is unchanged, renders stale shot spans. That second case is silent misalignment.
- **The manifest can't prove reuse.** It's overwritten every run and has no record of what was reused relative
  to the last good render. A failed re-render replaces it while the old `episode_final.mp4` stays.

A T1 re-plan rebuilds every shot's `assets`, dropping locks and notes (phase-4 follow-up). The re-edit paths
must never trigger one.

**Memory.**
- `season.json`'s `series_memory` is loosely typed (`schemas.py:1455-1469`).
- Hooks are plain strings. S3 closes them by an **enumerated schema value**, never by fuzzy matching.
- Store writes are atomic but there's no revision check. Copy `steps/season.py:186-192`'s re-read-then-write.

**N1 characters.** `cast` with `params.custom` already adds one character to a `ready` story. A lead or support
character clears `approvals.cast` (DEC-123), so the story drops out of `ready` until it's approved. Recurring and
guest roles don't.

**Styles.**
- Only Montserrat Black ships.
- `pan_pct` is ignored (a constant 4 in `render/motion.py:55`).
- Three of the five new styles use `two_line`, which neither MVP style renders.
- The template parity test (`tests/test_aistory_templates.py:246-285`) diffs §5 prose against the spec, so a
  prose fix edits the spec too.
- `test_aistory_templates.py:161` pins `version == 1`.
- `build_style_lock` deep-copies the template (`stylelock.py:139-170`), so locks are already immutable, but no
  test proves it.

**Tests that pin "phase 5" and change on purpose** (each edit named in the action log):
- `test_stories_api.py` `test_a_later_phase_step_is_a_400` (:660-671)
- `test_stories_api_phase4.py:99-109,136,367,551`
- `test_stories_api_phase2.py:419-433`
- `test_story_workflow.py:285-288`
- `test_story_workflow_episode.py:129,160`
- `test_stories_api_episode.py:241,496,539`
- "phase 5" strings in code: `cli.py:60,841`, `workflow.py:1721`, `routes/stories.py:982`, `SeasonStep.jsx:138`
- `test_story_cli_episode.py:185`
- `test_story_episode_steps.py:889`
- `test_story_prompts.py` registry pins (`PROMPT_VERSION` / `MAX_TOKENS` / `SCHEMA_NAMES`)
- `test_story_prompts_episode.py:1359`
- `test_story_episode_prompt_budgets.py:218,284`
- `test_aistory_templates.py:161`

## Stages

Tier-1 after every stage, in both environments:
- `python -m pytest -p no:warnings` (never `-q`);
- the CI environment (`PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs`);
- `compileall` with the cache prefix in scratch;
- `npm run build` to a scratch outDir (move `web/dashboard/dist/` aside first).

Every behaviour test is shown failing before the change. For behaviour that already holds, the proof is a
deliberate, reverted mutation, recorded in the log.

Each stage ends: green → commit (explicit paths, no trailers) → one log line → CHECKPOINT row. Model tier in
brackets.

### Stage 0 — checkpoint, baseline, worktree [inline]
- **Goal.**
  - Pull `main`.
  - Create worktree `.claude/worktrees/ai-story-phase-5` on `feat/ai-story-phase-5` (symlink `node_modules`).
  - Run the Tier-1 baseline. Phase 4's close was local 5643/1, CI 4936/677.
  - Back up the FR story `b1104ec66b05` and the EN story `0a9572a6a8be` as tars with their sha256.
  - Write the CHECKPOINT in-progress header with the hash, the regression contract and this plan's path.
  - Copy this plan to `~/.claude/plans/` on the VPS.
- **Files:** `.claude/*`.
- **Risk:** none.
- **Verify:** clean tree at a known hash; baseline recorded.
- **Rollback:** delete the worktree.

### Stage 1 — memory documents and the pure fold [Opus: data mutation]
- **Goal.** Memory becomes a **fold over per-episode S3 entries**:
  - `series_memory.entries[epNN] = {recap, hooks_opened[], hooks_closed[], relationship_deltas{pair: text},
    script_rev, at, approved_at}`.
  - The spec-shape fields `recaps`, `open_hooks` and `relationship_state` are **derived** by a pure
    `fold_memory(entries)` and stored, so `memory_section`, E4 and the spec-shape test read them unchanged.
  - Re-running memory for an edited episode replaces that one entry and re-folds, which is idempotent.
- **New typed fields**, all optional and backward compatible:
  - `entries`;
  - `audience_feedback[] = {ep, pasted_at, text, digest?, directions?[3], chosen_direction?: 0|1|2|null}`;
  - arc entry `history[] = {summary, open_hooks_out, replaced_at, source}`.
- **New doc** `episodes/epNN/proposals.json` (`next_proposals_v1`):
  - `for_ep`, `based_on{memory_ep, script_rev}`;
  - `characters[≤2]{item_id, name, role ∈ recurring|guest|support|lead, one_line, archetype?, why}`;
  - `twists[≤2]{item_id, target_ep, summary, open_hooks_out[], why}`;
  - `decisions{item_id: accepted|rejected}`.
- **Validation.** Stricter validators check caps (recap ≤ 40 words, hook ≤ 120 chars, pair keys sorted
  `char_a|char_b` over existing ids).
- **Cleanup on delete.** Deleting a character removes its relationship keys too (closes the phase-2 follow-up;
  the fold makes it one line).
- **Files:**
  - `clipping/aistory/schemas.py`, `store.py`;
  - new `clipping/aistory/series_memory.py` (fold, merge, stale check);
  - tests `tests/test_story_series_memory.py`, extensions to `test_story_entities.py`'s `SEASON_BREAKS`.
- **Risk:** medium. The season validator is read on every load, so a too-strict check could lock out the live
  seasons.
- **Verify:**
  - fold golden cases (order-independent re-run, hook closed only by exact value, unknown-pair refusal);
  - the live FR and EN `season.json` copies validate unchanged.
- **Rollback:** revert the commit (no data written yet).
- **Regression items at risk:** RC-A8 and RC-M3 (existing documents read).

### Stage 2 — prompts S3, F1, N1 [Sonnet]
- **Goal.** Builders `build_s3/f1/n1(pack, …) -> (system, user, schema)`:
  - **S3** (ANALYTIC, 250): recap ≤ 40 words; `hooks_closed` enumerated from the current open hooks;
    `hooks_opened` ≤ 3, seeded by the arc's `open_hooks_out`; relationship deltas over enumerated pairs.
  - **F1** (ANALYTIC, 250): digest ≤ 60 words plus exactly 3 directions.
  - **N1** (IDEATION 0.9, 350): ≤ 2 characters with role recurring or guest by default, ≤ 2 twists targeting an
    episode after N.
- **Registry.** Validators and repairs (French elisions, DEC-144). Registry entries in `MAX_TOKENS`,
  `TEMPERATURE` and `SCHEMA_NAMES`. `PROMPT_VERSION` bumped.
- **Caps.** Measured on live-sized worst-case data +15% (DEC-138).
- **Files:** `prompts.py`, `schemas.py`, `context.py`, `tests/test_story_prompts_series.py`, the registry pins in
  `test_story_prompts.py`.
- **Risk:** low.
- **Verify:** golden strings in FR and EN, schema-invert fakes, cap measurement recorded.
- **Rollback:** revert.
- **Regression items at risk:** RC-M1 (episode-1 prompts byte-identical).

### Stage 3 — continuity in the episode prompts [Opus: changes phase-3 prompt contracts]
- **Goal.**
  - **E1 (ep ≥ 2)** receives the previous recap, the open hooks (enumerated) and the chosen audience direction
    (labelled `audience`). It must mark `pays_off: [hook]` on at least one body scene.
  - **E3's recap scene** is written from the recap.
  - **E4** gains issue kind `hook_payoff`. The persisted closed list is widened, which is backward compatible.
    A deterministic Python pre-check runs too: at least one scene carries `pays_off` and the scene exists. E4
    judges whether its lines actually pay the hook off.
  - Input budgets are re-measured with memory and audience at their caps (DEC-138).
- **Episode 1 is untouched:** no memory, no `pays_off`, the same bytes.
- **Files:** `prompts.py`, `context.py`, `steps/script.py`, `schemas.py`, `tests/test_story_prompts_episode.py`
  (new ep-2 goldens), `test_story_episode_prompt_budgets.py`.
- **Risk:** medium. E1 validity on the free links with one extra field (bench in stage 13).
- **Verify:**
  - ep-2 E1, E3 and E4 goldens;
  - payoff pre-check unit tests;
  - `test_build_e1_golden_fr` and every ep-1 golden **unedited**.
- **Rollback:** revert.
- **Regression items at risk:** RC-M1, RC-E1, RC-A4.

### Stage 4 — steps memory, feedback, propose-next and the gate [Opus: data mutation, cross-cutting]
- **`steps/memory.py`**
  - Needs `script:<ep>` approved and fresh. One S3 call, then re-read → write the entry → re-fold, under the
    store lock. Ends `awaiting_approval`, approved via `memory:<ep>`.
  - A later script edit makes the entry **stale** (`script_rev` mismatch). That shows as a banner. It never
    cascades into episode N+1's approvals.
- **The gate (amends DEC-130).** Writing episode N+1's script or storyboard needs `entries[epN]` to be approved
  and fresh. The refusal names the exact missing piece.
- **`steps/feedback.py`**
  - Input is the pasted text stored by the endpoint (stage 5), with a hard cap of 6,000 chars at the API: refused
    over the cap, never trimmed.
  - One F1 call. Ends `awaiting_approval`. Approving `feedback:<ep>` takes `{direction: 0|1|2|null}`, which is
    stored as `chosen_direction` and used by E1 of the next episode only.
- **`steps/propose_next.py`**
  - Needs fresh approved memory for ep N. One N1 call writes `episodes/ep{N+1}/proposals.json`. Ends
    `awaiting_approval`.
  - **Accepting a character** queues the existing cast path (`params.custom`, `introduced_in = N+1`), which runs
    K1, sheets and voice, then records `series_memory.introduced`.
    - Its role defaults to recurring or guest, so the story stays `ready`.
    - Choosing lead or support is allowed, but the refusal text/UI says it folds cast approval (DEC-123
      unchanged).
    - E1 lists only approved characters.
  - **Accepting a twist** amends the target arc entry and pushes the old text to `history`. The acceptance *is*
    the approval, so `approvals.season` is not cleared.
  - **Rejecting** records the decision only.
- **Registration.** Add to `RUNNERS`, remove from `LATER_STEPS`, add to `_job_doc`, the approve grammar
  (`memory:<ep>`, `feedback:<ep>`, `proposals:<ep>`), the estimate table (1 LLM call each, free chain,
  `allow_paid` enforced).
- **Files:** `steps/{memory,feedback,propose_next,__init__,episode_common}.py`, `workflow.py`, tests
  `tests/test_story_series_steps.py`, and the moved "phase 5" pins.
- **Risk:** high.
  - Races with `approve_season` or a character delete: mitigated by re-read-then-write under `store._lock`.
  - The status fold: tests cover recurring vs lead.
- **Verify:**
  - Fake-LLM step tests: ep 2 refused → memory → approve → allowed.
  - A stale entry blocks a new ep-2 script but not the existing ep-2 approvals.
  - Accepting a twist keeps the season approval.
  - An N1 lead folds the cast approval and a recurring character doesn't.
  - No paid link without `allow_paid`.
- **Rollback:** revert.
- **Regression items at risk:** RC-E2, RC-M5, RC-M7, RC-A3.

### Stage 5 — series API and CLI [Sonnet]
- **Goal.**
  - `POST /steps/memory|feedback|propose-next` through the generic dispatch.
  - `POST /episodes/{ep}/feedback` with a JSON body, text `max_length` 6000 and optional stats text; it stores the
    text and queues the feedback step.
  - `POST /episodes/{ep}/proposals/{item_id}` `{accept: bool, role?}`.
  - `GET /stories/{id}` and `/episodes/{ep}` expose memory entries, staleness and proposals.
  - Estimates.
  - CLI: `--ai-story step <id> memory|feedback|propose-next --ep N` (`STEPS`, `_STEP_ONLY`, `_KEYED_STEPS`,
    `AUTO_APPROVABLE`) and `--ai-story feedback <id> --ep N --text-file F`.
- **Files:** `web/api/routes/stories.py`, `web/api/models.py`, `clipping/aistory/cli.py`,
  `tests/test_stories_api_series.py`, `tests/test_story_cli_series.py`, and the payload-contract tests.
- **Risk:** low.
- **Verify:** contract tests (text/AST), route tests, CLI tests.
- **Rollback:** revert.
- **Regression items at risk:** RC-A4, RC-A5.

### Stage 6 — whole-frame shot timing [Opus: changes DEC-142's timing source]
- **Goal.** Every scene duration from `timing.episode_pass` and every shot duration from
  `shots._time_shots`/`retime_storyboard` is a whole number of frames, stored as `round(n/30, 3)` so that
  `round(d·30) == n` exactly.
  - Cumulative rounding in the timeline then equals per-shot rounding.
  - A change in scene k moves later shots by whole frames only, so their `frames` and cache keys don't change.
- **Existing documents.** Stored ones render exactly as today, because the timeline is unchanged; only a re-time
  writes quantized values.
- **Files:** `timing.py`, `shots.py`, `tests/test_story_frame_stable_timing.py`, and named re-pins of exact
  3-decimal durations in the phase-3 timing tests.
- **Risk:** high, **the second riskiest**.
  - It moves phase-3 durations by less than one frame per scene.
  - It may shift the 55–80 s window decisions at the edges.
- **Plan inside the stage:** run a spike first that counts the affected pins. If more than about 15 phase-3 pins
  move, stop and re-plan with the rejected alternative below.
- **Verify:**
  - Property test: shifting scene k by any Δ leaves every later shot's `frames` and cache key identical.
  - The golden framemd5 is unchanged on all three keys.
  - A copy of the live FR ep01 renders byte-identical before any re-time.
- **Rollback:** revert.
- **Regression items at risk:** RC-M2, RC-M3, RC-E1.

### Stage 7 — re-edit operations [Opus: data mutation across script, board and assets]

**Today on `main`**, from the delta check:
- `patch_script` (`workflow.py:2646-2694`) resets the line's timing to estimated, clears the script **and**
  storyboard approvals, stales the scene, and makes the assets fingerprint stale (`assets.py:409-411`). The old
  mp3 stays on disk.
- `LineMeasurement.sync_storyboard` (`voice_lines.py:223-241`) already re-times the board without a revision
  change, but it skips the stale scene. Only a storyboard re-plan un-stales it, and a re-plan wipes every shot's
  `assets` (`shots.py:849,861-864`).
- One line edit therefore costs, today: E4 re-check → script approval → a re-plan (every image lost) →
  storyboard approval → assets → assets approval.

**Goal:** the minimum path per edit, with the gates kept.
- **Text-only line edit.** Same line ids and speakers, and the scene's shot plan unchanged. The scene is marked
  `retime_only`, not `stale`.
  - The script approval is still cleared and E4 must be fresh again (DEC-129: the words changed).
  - The **storyboard approval is kept**, and the scene is re-timed in place once the line is re-voiced. Shots keep
    their images, locks and notes.
  - This **amends DEC-129 for text-only edits**. A structural edit (lines added or removed, speaker changed)
    behaves exactly as today.
- **Re-voice one line.** `line:<ep>:<lid>` re-synthesises only that line.
  - Its note now reaches the voice direction instead of being ignored (`assets.py:1536`).
  - Its `take` is **persisted** in the line entry (closes the phase-4 follow-up: today it's lost at
    `assets.py:1559` / `worker.py:256`).
- **Shot image regenerate with a note:** phase 4's path (`assets.py:1461-1514`, fresh seed, DEC-154), unchanged.
- **Motion swap:** PATCH `camera_motion` keeps the image and changes only that shot's render key. DEC-141's
  refusal is kept.
- **Framing, action or prompt edit.** It outdates the image (`shot_state` → `stale`, `assets.py:558`).
  - **Bug to close:** `render.py:133-156` never checks `shot_state`, so after the storyboard is re-approved a
    render uses the old image. Render (and rerender) now **refuse** a stale shot image, naming the shot.
  - A framing edit runs `rule_pass`'s checks and refuses an edit that breaks the cross-scene rules, instead of
    moving a neighbour silently.
- **Transition change:** PATCH (already re-times, `workflow.py:2896`). No shot key changes.
- **Guard test:** no re-edit path ever calls `build_storyboard`. Every untouched shot's `assets` dict stays
  byte-identical.

**Stage details:**
- **Files:** `workflow.py`, `steps/episode_common.py`, `steps/voice_lines.py`, `steps/assets.py`,
  `steps/render.py`, `shots.py`, `tests/test_story_reedit.py`.
- **Risk:** high. Staleness bookkeeping across three documents, plus a DEC-129 amendment.
- **Verify:**
  - Per-edit unit tests assert what is stale, what is re-timed, what is kept and which approvals survive.
  - A render with a stale image is refused.
  - The phase-3 re-time tests (`test_story_workflow_episode.py:312,512,525`) pass unedited.
- **Rollback:** revert.
- **Regression items at risk:** RC-A4, RC-A6, RC-E2.

### Stage 8 — partial re-render (**RISKIEST**) [Opus]
- **Pure selector.** `render/partial.py`:
  - `select(baseline_manifest, new_plan, cache_dir) -> {rebuild[], reuse[], reasons{shid: image|motion|
    frames|modifiers|overlay|missing|corrupt}}`;
  - it shares the runner's hit predicate, strengthened to check the cached clip's recorded sha256, not only
    size > 0.
- **Baseline.** The last good render: the runner copies the manifest to `render_manifest.last_good.json` on
  success, so a failed run never becomes the baseline and the Preview's video always matches it.
- **Manifest.** An optional `reuse{baseline_output_sha256, shots_total, shots_rebuilt[], shots_reused[],
  reasons}`, backward compatible.
- **Step.** `rerender` uses the runner and needs a completed render plus current assets approval. It ends
  `completed` (DEC-161). The feed and the episode payload say "3 of 11 shots re-rendered".
  - It reuses `current_render` (`render.py:346-406`, which already rebuilds the plan and compares it), so "changes
    since last render" becomes a per-stage diff instead of today's single `out_of_date` boolean.
  - `PreviewPane.jsx:222` already shows "N/M shots cached". It switches to the `reuse` record.
- **Registry.** `rerender` leaves `LATER_STEPS` (`workflow.py:90`) for `RUNNERS` / `COMPLETED_STEPS`
  (`steps/__init__.py:103-127`). `import` stays for phase 7.
- **Metadata.** The pack goes stale through its existing `render_sha256` check and isn't refreshed
  automatically.
- **Correctness proof:** **partial == full**. A real-ffmpeg CI test parameterises the golden fixture (a line's
  duration and one image changed), runs a partial re-render over a warm cache and a clean full render of the same
  documents, and asserts identical framemd5, plus that only the expected `S:` stages ran.
- **Files:** `render/{partial,runner,manifest}.py`, `schemas.py`, `steps/render.py` (or `rerender.py`),
  `render/golden.py`, `tests/test_aistory_render_partial.py`, extensions to the golden test.
- **Risk:** highest: stale reuse would show an old frame silently.
- **Verify:**
  - A selection table test for every edit type from stage 7.
  - Fake-ffmpeg runner tests (baseline swap only on success; corrupt cache → rebuild).
  - Partial == full under real ffmpeg.
- **Rollback:** revert. The cache files are content-addressed and harmless.
- **Regression items at risk:** RC-M2, RC-A1, RC-A7.

### Stage 9 — re-edit API and CLI [Sonnet]
- **Goal.**
  - `POST /steps/rerender {ep}`.
  - A `GET /episodes/{ep}` "changes since last render" list and the dry-run count (the selector over a fresh
    plan: no ffmpeg, stats only).
  - The PATCH or regenerate targets from stage 7.
  - CLI `step <id> rerender --ep N`.
- **Files:** routes, models, cli, contract tests.
- **Risk:** low.
- **Verify:** contract, route and CLI tests.
- **Rollback:** revert.

### Stage 10 — dashboard: SeasonBoard series panel [Sonnet]
- **Goal.**
  - `SeriesMemoryPanel` (the stub at `SeasonStep.jsx:132`) goes live: recap per episode, open hooks, relationships
    as a readable list (names, not ids), a stale banner, and "Update memory".
  - A feedback box (paste → digest → pick a direction or none).
  - "Propose next episode" cards: accept or reject each item, with a role picker and the fold warning for lead or
    support.
  - Estimate chips.
- **Files:** `SeasonStep.jsx`, `api.js`, `test_dashboard_story_shared.py` `STORY_FUNCTIONS`, contract tests.
- **Risk:** low.
- **Verify:** build green, contract tests, and a browser check at 375/820/1280 on a copy.
- **Rollback:** revert.

### Stage 11 — dashboard: EpisodeStudio re-edit [Sonnet]
- **Goal.**
  - Line edit → "Re-voice this line".
  - Motion, framing and transition controls, where framing shows "needs a new image".
  - A "Changes since last render" list with a Re-render button and its estimate ("≈ 3 of 11 shots").
  - The Preview summary "3 of 11 shots re-rendered · 8 reused".
  - The stop reason visible on the first screen at 375 px (phase-4 follow-up, check first).
- **Files:** `EpisodeStudio.jsx`, `episode/{ScriptPane,StoryboardPane}.jsx`, `api.js`, contract tests.
- **Risk:** low.
- **Verify:** as stage 10.
- **Rollback:** revert.

### Stage 12 — fonts and per-style renderer gaps [Sonnet]
- **Fonts.** Ship the OFL TTFs the five templates name (Bangers, Luckiest Guy, …; the exact list is read from the
  templates, and **filenames, sources and sizes are confirmed with the human before download**) plus their
  `OFL.txt` under `assets/fonts/`. `fonts.resolve_font` resolves a template family from `assets/fonts/` before
  `custom_fonts/`.
- **`pan_pct`.** The template's `pan_pct` is plumbed into `motion`/`filtergraph`/`plan`. For `fruit_drama` and
  `family_3d`, the stage first checks their values equal today's constant, so their argv stays byte-identical
  (golden strings unedited).
- **Accents.** `two_line` legibility is checked for the cinematic_real and claymation palettes against DEC-169's
  rule.
- **Lock immutability test** (acceptance). It's a deliberate-mutation proof, since the behaviour already holds.
- **Files:** `render/{fonts,motion,filtergraph,plan}.py`, `assets/fonts/*`, `tests/test_aistory_render_fonts.py`,
  `tests/test_style_lock.py`.
- **Risk:** medium: the fonts must not change an MVP style's render (asserted).
- **Verify:**
  - Golden strings for the MVP styles unedited.
  - A libass proof per new font, checked by eye.
  - The immutability test.
- **Rollback:** revert.
- **Regression items at risk:** RC-M2, RC-M4.

### Stage 13 — merge, deploy, Tier-2 series walk (me at 375 px; the human watches ep 2 on the phone)
- **Merge and deploy.** Fast-forward `main`. Deploy with `rm -sfv backend` + `up -d --build` at 0 jobs.
- **Walk the FR story `b1104ec66b05`:**
  - (a) Ep-1 script approved → Update memory → recap, hooks and relationships look right → approve.
  - (b) Paste fake audience comments → digest and 3 directions → pick one.
  - (c) Propose next → accept 1 twist (the arc entry changes, the old text is in `history`, the season approval
    is kept) and 1 new character (K1 → sheets → voice → approve; `introduced.ep02`).
  - (d) Ep-2 script: E1 marks a payoff, E3 writes the recap in ≤ 3 s, E4 passes payoff → storyboard → assets →
    render → metadata.
  - (e) Re-edit 2 lines and 1 shot image → rerender. The manifest shows N rebuilt of M, matching the
    dry-run count and the expected shots.
  - (f) Ep 1 re-rendered with no edits is still byte-identical.
  - (g) No overflow at 375/820/1280.
  - (h) The ledger is $0.
- **Also:** a free-link bench for S3/F1/N1 and the ep-2 E1 (3 runs each).
- **Findings:** one commit per fix, at most two attempts each.
- **Risk:** as stages 3, 4 and 8.
- **Rollback:** ff-revert `main` to stage 0's hash and restore the tars.

### Stage 14 — Tier-2 per style (5 short episodes, free route)
- **Setup.** One story per style from its library concept (e.g. `clay_town_confessions` for claymation,
  `detective_dawn` for cinematic_real). Cast of 2, 1 place, prompt-only. Steps 1–7 via the CLI with
  `--auto-approve`, then the Fast track from the dashboard.
- **Budget.** About 30 min of pollinations pacing per style, so spread across sessions.
- **Per style, record:** length, loudness, subtitles/fonts, motion (handheld, jitter, pan), overlays, and what
  drifted or held → **one A-entry per style**.
- **Fixes:**
  - Go into the template JSON with `version` bumped; `test_aistory_templates.py:161` is re-pinned per bumped
    style, named in the log.
  - §5 prose fixes also edit the spec (parity test).
  - The immutability test shows the live stories' locks are unchanged.
- **Human:** watches at least one of the five on the phone and acknowledges the walk.

### Stage 14b — paid-path live test (the human funds the providers first)

Asked for by the human on 2026-09-29: "at the end of phase 5 I'll add funds to the desired providers, to make a test
with the existing functionalities".

It closes the paid steps deferred since phase 0 (the one paid Tier-2 call) and phase 4 (step 12: a paid estimate
refused by the cap).

- **Before running:**
  - The human names the funded providers and the spend ceiling for the test.
  - I show each chain's paid links, their prices (`pricing.py`) and the estimate per step.
  - Nothing runs until the human says go.
- **Walk.**
  - (a) With `allow_paid` off, the paid links are refused and the reason is printed.
  - (b) With `allow_paid` on and a cap below the estimate, the step is refused naming both numbers
    (`per_episode_cap_usd`).
  - (c) With the cap raised to fit, run one short episode's assets on the paid image/edit links, and one paid TTS
    line if a TTS provider is funded:
    - one submit per request (DEC-106);
    - the journal entry is booked at submit (DEC-151/153);
    - a forced poll failure followed by a Continue resumes rather than re-buying (DEC-152);
    - a regenerate with the same inputs is served from the gencache at $0.
  - (d) Every call is in `cost_ledger.json` and `spend.json`, and the ledger total matches the providers' own
    dashboards within rounding.
  - (e) The partial re-render and re-edit (stages 7–8) run on the paid assets.
- **Record:** an A-entry per provider (real price vs table, latency, failures).
- **Findings:** any bug found gets its own fix commit (at most two attempts).
- **Rollback:** switch `allow_paid` off and restore the story tars.

### Stage 15 — docs and decisions [Sonnet]
- `docs/AI_STORY.md`: step 13 (memory, feedback, propose-next), re-edit and partial re-render, styles, CLI.
- `VISION.md` "Where it stands".
- DECISIONS from DEC-174, ASSUMPTIONS from A-081.
- The export/import bundle recorded as a deliberate follow-up.
- CHECKPOINT close-out, per artifact.

## Regression contract (phase 5)

RC-P1…P11, RC-S1…S4, RC-T1…T3, RC-E1…E5 and RC-A1…A9 stay in force. Added:

| ID | Must keep working | Proven by |
|---|---|---|
| RC-M1 | Episode-1 prompts byte-identical (no memory, no `pays_off`) | `test_story_prompts_episode.py` ep-1 goldens unedited |
| RC-M2 | Golden render unchanged on all three keys | `tests/test_aistory_render_golden.py` + `framemd5.json` unedited |
| RC-M3 | Stored episodes read, validate and re-render byte-identical with no edit | stage-1/6 copy checks + Tier-2 (f) (live ep 1 sha) |
| RC-M4 | A `style_lock.json` never changes when its template does | new `test_style_lock.py` test (stage 12) |
| RC-M5 | Memory, feedback and propose-next never touch `story.status`/`approvals`; only an accepted lead/support folds cast (DEC-123) | stage-4 tests |
| RC-M6 | Clip mode untouched | `test_render_layer_guard.py` + `git diff --stat b60938e -- clipping/studio clipping/story` empty |
| RC-M7 | No paid call from the new steps without `allow_paid` | stage-4 tests + DEC-115 tests unedited |
| RC-M8 | A partial re-render's output equals a full render of the same documents | stage-8 real-ffmpeg test |

## Expected decisions and assumptions

**Decisions:**
- DEC-174: series memory is the only carrier of continuity between episodes.
- DEC-175: memory is a fold over per-episode S3 entries; re-running is idempotent.
- DEC-176: memory runs on an approved script, goes stale on a script change, and gates episode N+1 (amends
  DEC-130).
- DEC-177: hooks are closed by an enumerated value; E1 marks `pays_off`; E4 checks it.
- DEC-178: audience feedback is pasted, capped and never trimmed; a chosen direction steers only the next E1.
- DEC-179: N1 proposals; recurring/guest by default; accepting a twist is its approval, with `history`.
- DEC-180: shot timing in whole frames.
- DEC-181: the partial re-render baseline is the last good manifest; partial == full.
- DEC-182: re-edit rules.
  - A text-only line edit keeps the storyboard approval and re-times in place (amends DEC-129).
  - A framing swap outdates the image, and a stale image is never rendered.
  - A rule violation is refused.
  - A voice regenerate keeps its note and persists its take.
- DEC-183: the per-style OFL fonts ship; the template version bump rule.

**Assumptions:** A-081+, one per style from stage 14, plus S3/F1/N1 JSON validity on the free links.

## Rejected alternatives
- **Memory as incrementally merged state:** re-running memory for an edited episode would double-apply its
  deltas. The fold is idempotent.
- **Frame anchoring in the timeline instead of whole-frame timing at the source:** audio and video would drift up
  to n/60 s and the timeline's total check would have to loosen. It stays the stage-6 fallback if the spike shows
  too many moved pins.
- **Fuzzy matching hook text:** it's non-deterministic. Use an enumerated schema.
- **A separate renderer path for re-renders:** the runner already reuses by key. Only the baseline, the report and
  the proof are new.
- **The export/import bundle:** deferred at the human's call.

## DECISIONS check
- **Amended:**
  - DEC-130 (the gate);
  - DEC-142 (the single timing source is kept but quantized);
  - DEC-129 (text-only line edits keep the storyboard approval).
- **Respected unchanged:** DEC-108/161 (step terminal states), DEC-115, DEC-123, DEC-138, DEC-141, DEC-144,
  DEC-154, DEC-155, DEC-164, and the auth task's DEC-173.
- No other conflicts.

## Riskiest stage
**Stage 8, the partial re-render.** A stale reuse would ship an old frame silently. It's guarded by the
partial == full real-ffmpeg test and the sha-checked cache hit. Stage 6 is second.

## Verification (end to end)
- Tier-1 green in both environments at every stage.
- Tier-2 stages 13–14 on the live server at 375 px, with the human watching ep 2 and at least one new-style
  episode on the phone and acknowledging.
- Acceptance:
  - ep 2 opens with the recap and pays off an open hook (E4 passed, `pays_off` present);
  - the partial re-render manifest lists only the changed shots as rebuilt;
  - fail-first shown for the S3/F1/N1 goldens, the memory fold, the payoff check, the partial selection and the
    lock-vs-template test.
