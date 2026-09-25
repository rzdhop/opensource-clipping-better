# Phase 3 prompt — episode writer: script, storyboard, timing, consistency check

Paste this into Claude Code from the repository root. Phase 2 merged and acknowledged.

---

Continue under the repository protocol: read `.claude/VISION.md`, `.claude/DECISIONS.md`,
`.claude/ASSUMPTIONS.md`, `.claude/CHECKPOINT.md`, then
`.claude/plans/ai-story/00-MASTER-SPEC.md` in full. Follow spec section 0.

## Goal of this phase

Workflow steps 8–9 (spec section 3): a complete, approved `script.json` and
`storyboard.json` for an episode, with computed timing — no asset generation yet
(voices are synthesised only to measure durations if the user opts in; otherwise a
deterministic estimate is used and flagged).

1. **Episode templates** `templates/episodes/serial_60s_v1.json` (55–80 s) and
   `serial_90s_v1.json` (75–100 s) (spec 6.2) loaded and validated; `hook_style`,
   `cliffhanger_style`, `shots_per_scene`, `max_places` read from the style lock's
   `episode_defaults`; closed lists of spec 6.3 as enums mirrored in argparse `choices=`
   where a CLI flag exists (DEC-009: no invented numeric bounds beyond the template's own).
   Read `10-REFERENCE-ANALYSIS-2026-09-25.md` for the measured pacing these encode.
2. **Script prompts** (spec 4.2): **E1** beat sheet (8–12 stubs, functions from the
   closed list incl. `recap` for ep ≥ 2, `target_duration_s` hints only), **E2** per
   **body** scene (1–4 lines ≤ 22 words, `speaker` must be a cast id present in the scene
   or `"narrator"` when the story enables one, emotion from the closed list, free-text
   delivery, sfx cues from `style_lock.audio.sfx_cues`, optional on-screen text; context
   includes the previous scene's summary and last line), **E3** for the framing scenes
   (hook scene lines + on-screen text ≤ 6 words, cliffhanger reveal + ≤ 1 line with
   `cut_to_black`, recap scene line for ep ≥ 2 from `series_memory.recaps`, next-episode
   teaser — E2 never writes those scenes; the end card text is deterministic Python),
   **E4** consistency check (analytic temperature) over the whole script against bible +
   series memory → `consistency_report`. Golden-string tests for every builder; schema
   validation of every response; the writer never outputs durations, paths or
   timestamps.
3. **Timing engine** `clipping/aistory/timing.py` (spec 6.4): scene duration from line
   durations + pauses/tails, **per-slot clamps read from the episode template**, episode
   window 55–75 s, overflow (shorten tails → flag lines) and underflow (extend holds →
   flag) handling — pure functions, exhaustively unit-tested. Line duration source stored
   per line (spec 6.4 order of truth): real TTS (opt-in "measure with real voices", cost
   estimate shown) or the deterministic estimate `chars × rate_per_char[language]`
   (constants, labelled "estimated" in the UI, superseded in phase 4 when audio exists).
4. **Storyboard** (spec 2.8): **T1** per scene (2–4 shots per `episode_defaults`, min
   0.8 s reaction cuts; framing and camera motion
   from the closed lists, optional modifiers; action sentence; subject tags `@char`,
   `#place:variant`, `%prop`; context includes the previous two shots' framing/motion)
   or the deterministic "fast" path; then a **deterministic rule pass** enforces the
   cross-scene rules of spec 6.2 (no two consecutive identical framings, reaction
   close-up every 3 scenes, push-in on peaks) whichever path was used. Tag resolution →
   `reference_images` list; **fully resolved `image_prompt`/`negative_prompt`** via
   `prompting.py` (spec 5 skeleton with per-shot `framing_phrase`/`lens_phrase` tables)
   — golden tests on a fixture story; transitions by the grammar of 6.3; `motion` from
   the style's `motion_rules.tier1`; `duration_s` from the timing engine.
5. **Gating**: script cannot be approved while `consistency_report.passed` is false
   unless "approve anyway" (stored). Storyboard approval requires an approved script.
6. **Endpoints** (spec 9.2): `/steps/script|storyboard` (`ep` param),
   `GET /episodes/{ep}`, `PATCH /episodes/{ep}/script` (inline line edits → re-time),
   `PATCH /episodes/{ep}/storyboard` (prompt/motion/transition edits),
   `/approve/script:<ep>|storyboard:<ep>`, `/regenerate` targets from the spec 9.2
   grammar: `scene:<ep>:<sid>`, `hook:<ep>`, `cliffhanger:<ep>`, `teaser:<ep>`,
   `shot:<ep>:<shid>:plan`; estimates for the opt-in TTS measurement.
7. **Dashboard** (spec 10): `EpisodeStudio` with *Script* pane (scene list, inline
   editing, consistency issues inline, duration bar showing the 55–75 s window and
   flagged lines) and *Storyboard* pane (shot cards without images yet: framing, motion,
   prompt accordion, transitions; regenerate/lock). Preview pane placeholder. Phone
   layout checked.
8. **CLI**: `--ai-story step <id> script|storyboard --ep N [--auto-approve] [--fast]`.
9. **Docs + decisions**: `docs/AI_STORY.md` steps 8–9; DEC for "timing is computed in
   Python from audio, never by the model"; DEC for the deterministic duration estimate;
   A-entries on the free chains' behaviour with the E-prompts (JSON validity rate per
   provider, measured with the existing bench approach).

## Out of scope

Image/voice asset generation for shots, rendering, metadata, series memory update.

## Before you plan

Ask only what remains open. Propose stages (riskiest: E2 loop across 8–12 scenes on
free providers with time budgets), regression contract (phases 0–2, clip mode),
expected DEC/A, and the Tier-2 script: on the `ready` FR story, write episode 1 on the
free LLM chain, inspect the E4 report, edit two lines inline and watch the duration bar
re-time, build the storyboard both ways (T1 and fast), approve both; every LLM call
printed its hop and respected the token caps; total LLM calls for the episode counted
(expect ≈ 1 + N + 1 + 1 + N).

## Acceptance

- Tier-1 green in both envs; new tests shown failing pre-change (E1–E4/T1 golden
  strings, speaker-in-scene validation, timing math incl. overflow/underflow, xfade
  transition grammar, prompt resolution, approval gating).
- `script.json` and `storyboard.json` validate; every shot has a fully resolved prompt,
  reference list, motion and duration; episode total inside the window or flagged.
