# Plan 28 — the one-click episode: fits every time, ≤ $2 of API money, strict consistency, no TTS voices, Approve all, generated concepts only, every writer prompt on the plan-26 standard

Status: SHIPPED 2026-10-06 — approved on the human's "1) me + gen button 2) remove, Go" (DEC-305); every stage merged, documented and deployed (a271439; DEC-306 records the calls made on the way).

## 1. What happened (the trigger) and the root causes

Job 13bbb11a5896, story d71852710962 "Cœurs Sous Clé" (fr, fruit_drama, v2, tier 3, route api, budget_profile
native_speech, speech_model fast, narrator on, episode template serial_60s_v2, mode agent), 20:38–20:44 UTC:
"Fast track stopped at the storyboard (step 2 of 6): Episode 1 runs 84.0 s … 9.0 s over its 55–75 s window".

1. **The line plan was infeasible before any writer ran.** On Veo the clips sell 4/6/8 s and plan 27's 5–10 s
   window keeps 6/8. A narrator line never shares a character's clip (DEC-276 audio rule; `timing.py:795`,
   `shots.py:2960`). So every body scene = one 6 s narrator clip + one 6 s character clip = 12 s inside a 5–11 s
   slot; `timing.scene_plan` (`timing.py:829-856`) steps down, finds nothing and *breaks* instead of refusing.
   Hook 6 + 6 × 12 + cliffhanger 8 = 86 s planned; the storyboard made 84 (the writer put character lines in the
   hook and the cliffhanger, which `validate_e3_v3` never checks against the plan). Floor after take trims: 76 s.
   No generation outcome could fit 75 s.
2. **Two clocks, never reconciled.** The script step and `script_refusal` time the text (56.9 s, "ok",
   `timing._episode_pass`); the storyboard gate sums the clips (`native_speech.native_pass`, 84.0). The fast track
   approved on the first clock and was refused on the second. `native_pass` hard-codes every scene "ok"
   (`native_speech.py:453`) and emits only `episode_over`, which the Trim button ignores.
3. **No episode-level feasibility check anywhere** (`apply_e1`, `episode_slots`, `fast_track.estimate`): the
   scene count (8) comes from the template alone (`timing.episode_slots`), blind to the link's floors and the
   narrator. `serial_60s_v2` was never re-slotted by plan 27 (min_shot 3, body [5,11]); only narrated_drama and
   confrontation were. A native profile is *suggested* confrontation but the story was created on serial_60s_v2.
4. **No remedy loop.** On "over" the fast track stops; "Continue" is a deterministic dead end (nothing stale to
   re-plan, the same refusal). Fill runs on "under" only; the trim pass fires on word-cap errors only.
5. **A dead first link burned 152 s.** nemotron-ultra returned 500/503 on 24/24 attempts (3 attempts × 4 s + 12 s
   backoff per scene); nothing remembers a failed link between calls (`llm_call.call_json` re-resolves the chain
   every call). nemotron-super then truncated or failed validation; the OpenAI-compatible path never logs
   `finish_reason`, so cap hits are invisible.
6. **The money promise is wrong for API clips.** 14 shots on Veo: ≈ $7.2 (fast + lite), ≈ $4.8 (all lite);
   the repo's own estimate says $6.08. The live per-episode cap ($2) would have stopped at step 3 anyway.
   ≤ $2 is only true on the manual-clips profile (`native_speech_manual`): keyframes + writing ≈ $0.6–1.0.
   The pre-click estimate leaves the clips unpriced until the storyboard is approved (`fast_track.py:1144`).
7. **Also found:** no STT key on this host (GROQ/MISTRAL) so the take check is `stt_unavailable` and never
   retakes; fal balance was exhausted earlier today (keyframes fell to gemini); cast sheets are never judged;
   J2 is bypassed by "anyway" and by the fast track; the manual path has no consistency gate.

## 2. Open questions (answers change scope — block)

- **Q1 — the $2 promise.** (a) *Recommended:* the one-click path makes clips on your subscriptions (Flow /
  Higgsfield) by default — the app does writing, sheets, keyframes, prompts, the Handoff, the take check and the
  render; its own bill ≈ $0.6–1.0 per episode, hard-capped at $2. API clips stay a per-story opt-in with the real
  price shown before the click (≈ $4.8 lite / $7.2 fast for 14 shots). (b) Or: API clips on Veo lite with the
  per-episode cap raised to ≈ $6. (c) Or: ≤ 4 API clips per episode, the rest stills — not "every shot
  animated" (DEC-219).
- **Q2 — the narrator and voices.** "No voice generation, never edge" means TTS has only one remaining use on a
  native-speech story: the narrator's lines. *Recommended:* new stories start with the narrator OFF (its lines
  become on-screen text when a template needs narration); no voice pin, no sample, no voice UI on native-speech
  stories; `edge` removed from the default TTS chain everywhere (legacy tier-1/2 stories keep gemini TTS / local
  engines because their renderer needs line audio). Confirm: narrator off by default, and legacy stories keep a
  non-edge TTS?
- **Q3 — the template.** Did you pick "Serial 60 s" by hand in the wizard, or did the form suggest it? Either
  way the plan makes an infeasible template × link × narrator combination impossible; the question decides whether
  the wizard's suggestion logic also gets fixed (A-UNCONFIRMED otherwise).
- **Q4 — concepts.** *Recommended:* keep the 14 shipped concept files and their tests, hide the library from the
  API reply and the Concepts screen (generated cards only, "Generate 10 more" unchanged), stop seeding the avoid
  list with library titles. Or delete the library outright (CLI `--concept` choices and ~12 test fixtures change).
- **Q5 — strict consistency, the cost side.** Making J2 a hard gate needs the redraw budget sized to the episode
  (shots × 2 × $0.04 ≈ $1.1 for 14 shots instead of $0.40) and a free sheet judge on the cast step (flash-lite,
  $0). Accept the redraw ceiling inside the $2?
- **Q6 — writer prompts.** Upgrade on v2 stories only (legacy goldens stay byte-identical, as DEC-303 did)?
  *Recommended:* yes.

## 3. Stages (ordered; one commit each; DEC-234 selections in both envs; worktrees off main)

### Track A — the one-click path fits every time (riskiest track)
- **A1 Feasibility before any spend.** `timing.plan_clip_floor_s(script, tpl)` (pure) sums the plan's clips;
  called after E1 in `script._Run.beat_sheet` → `StepFailed` naming total / window / options; and a zero-LLM
  pre-check in `fast_track.estimate` + `script.run` from `episode_slots` × the story's real lengths × narrator.
  Files: timing.py, steps/script.py, steps/fast_track.py, tests/test_story_timing_plan.py (+ the 11 s / (6,8)
  case that is deliberately unpinned today). Risk low. Rollback: revert. RC: test_story_fast_track*, length gate.
- **A2 The planner keeps its contract and the episode fits.** `scene_plan` never stores clips over the slot
  (re-plan narrator-only / no-narrator instead of `break`); episode-level fit: with a narrator on, at most k
  two-clip body scenes so Σ clips ≤ window_hi (`character_line=False` on the rest, peak/turn kept); scene count
  from the floors when the template cannot fit (episode_slots gains the link floors). Re-slot `serial_60s_v2` /
  `serial_90s_v2` to the 5–10 s window (min_shot 5). Files: timing.py, steps/script.py, templates/episodes/*.json,
  episodeTemplates.js, tests (timing_plan, episode_schemas, native_speech_plan, payload_contract). **Riskiest stage.**
- **A3 One clock + a remedy loop.** On a native story the script-stage timing reads the plan's clips (so
  `script_refusal` and the storyboard gate agree); `native_pass` emits `scene_over` per scene; the fast track on
  "over" re-plans (A2's assignment) and regenerates the longest scenes once before stopping; the UI Trim button
  handles the native flags. Files: timing.py, native_speech.py, episode_common.retime, fast_track.py,
  DurationBar.jsx. Risk medium (episode_pass shared with the render timeline; goldens).
- **A4 Creation refuses the impossible.** `store.new_story` / the wizard: a native-speech story is created on a
  template whose floors fit (narrated_drama / confrontation), or the form shows the floor arithmetic and refuses;
  the "How clips are made" default follows Q1. Files: store.py, defaults.py, NewStoryWizard.jsx, stories.py.
- **A5 Dead links cost nothing.** Per-job `link_health` on `StepContext`, read/written in `llm_call.call_json`
  (a link that failed its ladder is skipped for the rest of the job, one half-open retest; a `⏭` log line);
  `finish_reason` + `completion_tokens` logged on the OpenAI-compatible path; the story chain order for the
  one-click path: gemini flash-lite (free) → openrouter mistral-medium (paid, cents) → nemotron ultra/super last
  (amends DEC-224). Files: steps/__init__.py, steps/llm_call.py, providers/llm.py (log only — RC-S4 exception
  recorded), providers/registry.py, tests/test_story_llm_chain.py, test_llm_negotiation.py.
- **A6 An honest number before the click.** The fast-track estimate prices the clips from the plan before the
  storyboard exists (shots × sold length × link price); the missing STT key and an exhausted fal balance are
  shown as pre-click warnings. Files: fast_track.estimate, media_policy.native_speech_estimate, stories.py, the
  FastTrack card.

### Track B — no TTS voices
- **B1** Narrator off by default on new stories (Q2); a profile flag `voices: none` on native-speech stories:
  `cast.run` skips `_pin_voices/_pin_narrator/_samples`, `character_missing` drops voice + sample, `cast_units`
  drops `tts_chars`, the Cast tile hides the voice UI; refused on non-native stories (their renderer needs audio).
- **B2** `edge` removed from `DEFAULT_CHAINS[TTS]` and `voices.json`; the Clips-mode voiceover keeps its own
  dependency (not AI Story). Tests: test_story_voices, test_generation_chain, test_story_cast_steps (Eloise pin).

### Track C — Approve all
- **C1** Cast / Places / Props: a client-side "Approve all" (sequential `approveStoryDoc` over complete,
  unapproved entities; stops on the first 409; never `approve_anyway`). Pin beside
  test_dashboard_generate_episode.py; api.js function list pin.
- **C2** Episode: "Approve script → storyboard → keyframes" next to the stepper, reusing ReviewPane's
  `ApproveAll` pattern; a server `POST /{id}/approve-all/{cast|places}` extracted from
  `story_fast_track.approve_entities` + `cli._approve_complete` into `workflow.approve_complete` (atomic).

### Track D — concepts
- **D1** Library hidden (Q4): `list_concepts` returns `library: []` behind a default-off flag; ConceptsStep shows
  the Generated section only; the avoid list no longer seeded with library titles; C1/C1v2 receive the setup
  context block of E1 (universe, species world, audience, format).

### Track E — every set-up writer prompt on the plan-26 standard (v2 only)
- **E1** `context.setup_context(story, lock)`: SERIES, ART STYLE (rendering sentence, palette line, hexes),
  universe + species pool, language, audience, format (window, 5–10 s shots, native speech, narrator). One new
  Pack section; own `INPUT_BUDGET` rows.
- **E2** Wired into B1–B3, P0/P1/R1, D1/D3/R1v2, S1/S2 (+ `window_s` seconds), D4–D6, C1v2; `NATIVE_LINE_V3`
  "at most 8 seconds" → the 5–10 s window; `_french_block` on fr set-up prompts; PROMPT_VERSION bump; goldens
  re-recorded for the v2 paths only.

### Track F — strict consistency rules
- **F1** J2 is a hard gate: no "anyway" on a failed or unjudged verdict; the fast track stops instead; redraw
  budget = shots × 2 × link price (Q5). F2 The J2 ask gets the head/species line and sees the place plate and the
  prop images. F3 A sheet judge on the cast step (one head, species match, no human head in a species world, two
  views on a two-view sheet) gating `approve_entity` and the CLI. F4 T1v2: the speaker's tag and every scene
  character must be in `subjects`; prop handles validated before the core. F5 A story-level provider lock for
  sheets/plates/props (refuse, never switch). F6 A keyframe whose time variant has no plate is refused (no silent
  day fallback); wardrobe continuity checked against the ledger at the script step. F7 The Handoff shows the J2
  verdict per shot and refuses the zip/upload until the keyframes are current.

### Track S — simple screens (the human, 2026-10-05: "the UI became too complicated, too much term I do not understand")
- **S1 New story = four choices.** The idea, the language, the look (style cards with a picture), and how the
  clips are made (two cards: "I make them on Flow / Higgsfield — about $1 of app cost per episode" or "The app
  makes them — about $5 per episode"). Everything else (tier, route, budget profile, pipeline, speech model,
  consistency mode, universe, frame, episode format) is decided by the app from those four and hidden behind one
  "Advanced" fold. The format is chosen so the clips always fit (A4). Files: NewStoryWizard.jsx, HowMadeControls,
  stories.py (the offer), media_policy.new_story_profile.
- **S2 Plain words everywhere the human reads.** Step names, chips, warnings, refusals and job-log lines written for
  a non-technical reader: no "T1", "J2", "tier 3", "native speech", "budget profile", "v2", link ids. A short
  glossary-free copy pass over the workspace, the episode studio, the Handoff and Settings; internal ids stay in
  the API and logs' debug level only. Files: the dashboard copy, gates.py sentences, fast_track.stopped text.
- **S3 One button per step.** "Make episode 1" (the one-click run), "Approve all" (track C), "Regenerate", and the
  Handoff's "Copy" and "Upload". Hide per-shot modes unless the human opens a shot.

### Track G — docs + close-out
- docs/AI_STORY.md (the one-click promise, the money table, the strict rules, no voices, Approve all,
  generated concepts), VISION, DECISIONS (DEC-305…), ASSUMPTIONS, CHECKPOINT, action log. Deploy at 0 jobs.

## 4. Rejected alternatives
- Widen the window (55–90 s) or raise the per-episode cap to make tonight's plan pass: hides the planner bug, breaks
  the format's rhythm (DEC-275/298) and the $2 promise.
- Merge the narrator's line into the character's clip audio: Veo would voice the narration with the character's
  voice; DEC-276's audio rule exists for that reason.
- Delete the concept library files: ~12 test fixtures and the CLI `--concept` choices depend on them; hiding is
  reversible.

## 5. DECISIONS check
Touches DEC-224 (chain order — amended by A5), DEC-268 (template suggestion — hardened by A4), DEC-276 (narrator
TTS over a silent clip — B1 makes it opt-in), DEC-122 (one-link voice — unchanged for legacy), DEC-230/243 (J2 and
auto-fix — F1 makes them hard), DEC-280 (provider mixing recorded — F5 ends it), DEC-298/300/304 (the plan — A2/A3
restore their contract), DEC-219 (every shot animated — Q1 (c) would conflict; (a) keeps it on the human's
subscriptions). No conflict with DEC-173 (no auth), DEC-234/278 (test policy), DEC-297 (pin files).

## 6. Riskiest stage
A2 (the planner's episode-level fit and the re-slotted serial templates): it changes pinned plan shapes on every
native story and the template values the dashboard mirrors. Guarded by A1 (which refuses before spend regardless).
