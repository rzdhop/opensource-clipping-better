# 21 — Plan: agent mode (U3; task C of DEC-263)

Date 2026-10-04. Approval: the human's "decide for me and go" (DEC-263). EXPLORE: one Sonnet agent (its map is
summarised in the action log). Brief: `18-competitive-analysis-2026-10-04.md` §5 U3 — one click (or one CLI
command) takes a new story from a one-line idea to episode 1 rendered with its metadata pack, under one shown total
estimate and the caps. Studio mode (today's gates) stays the default.

## Facts the design rests on
- The per-episode fast track already runs five steps inside one job by calling each step's `run(ctx)` and
  approving with `workflow.approve_*(by=FAST_TRACK_APPROVED)`; it keeps no state of its own — a stopped job is
  simply run again and each step fills what is missing (`fast_track.py:16-26, 721-885`).
- Pre-production approvals are mechanical completeness (bible, style, season, knowledge, the cast/places folds) or
  human taste (the style lock's three images, portraits, plates). Only script (E4/J1) and keyframes (J2) have judges.
- Defaults that already exist: the style from `concept.style_fit.default`; voices pinned by `cast.run`; places and
  props from the saved proposal; `DEFAULT_EPISODES = 8`. Missing: a concept pick (cards carry no score), a cast pick
  (`cast_request` refuses an empty one), a summed estimate, a story-level time budget, a rail status for a chained job.

## Decisions on the explorer's open questions
1. **The idea is the concept.** Agent mode takes the seed text; the concept step is asked for **one** concept
   (`params.count: 1`, a new param; the step's default stays 10) and it is chosen. No ranking is invented.
2. **Cast = the concept's `cast_sketch`, up to 5** (then `MAX_CAST`); fewer when the sketch names fewer.
3. **One shown total:** `workflow.story_fast_track_estimate` = the existing `cast_units` + `places_units` +
   `knowledge_calls` + `fast_track_estimate(ep 1)`; the $ caps stay per episode / day / story; a story-level time
   budget = the sum of the parts' budgets with the fast track's ceiling rule.
4. **Looks are approved on completeness, labelled.** The form says so: "Agent mode approves the style, the cast
   and the places as soon as they are complete — no taste check; review them in Studio afterwards (every regenerate
   stays available)". Script and keyframes keep their judges and the fast track's rules (DEC-265).
5. **The rail shows the sub-step:** the job record gains `sub_step` (set by the runner before each part); the
   workspace maps it to the rail key; the feed keeps the "⏩ Agent n/9: …" lines.

## Stages

### Stage 1 — `story-fast-track`: the runner and the estimate (backend)
- `steps/story_fast_track.py` (the `_FastTrack` pattern): concepts (count 1) → choose → bible → approve → style
  (`build_style`) + style_preview → approve → cast (the sketch pick) → approve each → places_proposal → places →
  approve each → season (8) → approve → knowledge (v2) → approve → `fast_track.run` for episode 1 with the
  story's `episode_template_id`. Each part idempotent ("kept as it is"); stops on a refusal, a cap, a key gate, or
  the fast track's own stops; "Continue" = run the same job again. `workflow.story_fast_track_estimate` and the
  budget. `POST /steps/story-fast-track` on a story created with `mode: "agent"` (a new `StoryCreateRequest`
  field, stored as `generation_profile.mode`, default `studio`); `GET /estimate/story-fast-track`.
- Tests: `tests/test_story_fast_track_story.py` (fail-first): the chain on fakes, idempotence after a stop at each
  part, the estimate sum, the refusals, RC-E2's sibling for the new step ("the story-level job writes
  `story.json` approvals only through `workflow.approve_*` with `by: agent`"), RC-A3 (every paid part through the
  caps).
- Risk — the riskiest stage: the approvals of eight documents from one job (RC-E2 is for episode steps; this step
  gets its own contract line RC-G1). Rollback: revert.

### Stage 2 — the CLI
- `--ai-story new --mode agent --seed "…" --style fruit_drama --format narrated_drama_60s_v2` then
  `step <id> story-fast-track` (or one `agent` command doing both), `--settings` honoured; the concept `count`
  param; `--auto-approve` semantics unchanged for Studio.
- Tests: `tests/test_story_cli_agent.py`.

### Stage 3 — the dashboard
- The new-story form: a "Mode" choice (Studio default / Agent, with the label of decision 4) sent as `mode`;
  on an agent story the workspace shows one "Run the agent" button with the summed estimate and the caps, the rail
  follows `sub_step`, the feed as today; the Review tab at the end as the fast track's. Payload contracts re-pointed
  where a literal moves.
- Tests: the wizard contract (`test_story_payload_contract.py`), a rail test for `sub_step`, the estimate card.

## Verification
Tier 1 per stage, both envs. Tier 2: one agent-mode story from a one-line fruit-drama idea to episode 1 (≈ $4–5
with the cast and places), judged on the phone.

## Rejected
- A concept ranker (another judge for a question of taste; the idea is the concept).
- Running agent mode without a summed estimate (every paid call is shown before it runs: DEC-174's rule).

## DECISIONS check
DEC-131 (the fast track precedent), DEC-162/246/265 (the fast track's approvals), DEC-173 (no auth), DEC-174/223
(caps and estimates), DEC-263 (order). RC-E2 keeps its scope; the new step gets RC-G1.

## Riskiest stage
Stage 1.
