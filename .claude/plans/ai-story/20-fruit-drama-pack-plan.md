# 20 — Plan: the fruit-drama pack (U4; task B of DEC-263)

Date 2026-10-04. Approval: the human's "decide for me and go" (DEC-263). EXPLORE: one Sonnet agent (the context
map is in this session's action-log line); the brief is section 5 of `18-competitive-analysis-2026-10-04.md`.

## What already exists (do not rebuild)
- The **recap beat** on ep ≥ 2: every episode template has `recap_from_episode: 2`; `timing.episode_slots`
  prepends it; `script.py` writes it as `s00`; the storyboard plans one beat shot.
- The **"PART N" end card**: `render/subtitles.py:780 end_card_ass` burns "PART {n+1}" / "PARTIE {n+1}" and the
  title (the golden fixture never exercises it).
- The **narrator** is on for every v2 story (DEC-231); narrator lines are never in frame, so they get no lip-sync.
- `fruit_drama.json` already lists the BGM moods `telenovela_tension`, `tropical_drama`, `suspense_sting`; two
  archetype concepts are shipped (`tentafruit_island` = rigged contest, `orchard_inheritance` = inheritance).
- The metadata pack's pinned comment already says "PART {n} →" / "PARTIE {n} →" deterministically.

## Decisions on the explorer's open questions
1. Narrator-led = **a new episode template** `narrated_drama_60s_v2` carrying a `narrator_share: [0.6, 0.85]`
   field (word share), which E2v2 turns into one ask line; the field is absent from the other templates, so their
   asks and the ep-1 goldens stay byte-identical (RC-M1).
2. Picking the Fruit Drama style **suggests**, never forces: the new-story form pre-selects v2 + the narrated
   template when the style's `episode_defaults.episode_template_id` names it; the user can change it; stored on the
   story as `episode_template_id` (the field exists on the story document already).
3. `fruit_drama.json`'s vestigial `episode_defaults.episode_template_id` (schema-const `serial_60s_v1`, never read)
   becomes a real suggestion: the const is lifted to the enum, `defaults.episode_template_for` reads the story's
   own `episode_template_id` first, then the pipeline default as today. The seven style ids, their prose and
   palettes stay exactly as pinned.
4. CTA wording: EN `Comment "PART {n}" for the next one →`, FR `Commente « PARTIE {n} » pour la suite →` in the
   pinned comment; the same line on the end card under "PART N", **opt-in per template** (`end_card_cta: true` on
   the narrated templates only), so the golden render fixture is untouched (RC-M2).
5. BGM: no new audio is shipped (no licence can be recorded from this repo); the narrated template prefers
   `telenovela_tension` for tension and `tropical_drama` for the recap; sourcing a licensed telenovela set is a
   follow-up outside the code.

## Stages (one worktree each off main after plan 19 is merged; merge order 1 → 2 → 3)

### Stage 1 — the narrated templates and the style's suggestion
- New `templates/episodes/narrated_drama_60s_v2.json` (from `serial_60s_v2`: fewer, longer body passages,
  `narrator_share: [0.6, 0.85]`, 2–4 character lines an episode, `end_card_cta: true`, BGM preferences) and
  `serial_90s_v2.json` (the 60s v2 shape at a 80–100 s window). `prompts._build_e2`: when the template carries
  `narrator_share`, one ask line tells E2v2 the narrator carries that share of the words and the characters speak
  2–4 short lines; E1v2's beat sheet gets the same hint. `defaults.episode_template_for` reads the story's
  `episode_template_id` first. `schemas`: the template enum (`:657`, `:2601`) and the style's `episode_defaults`
  const lifted to the enum. `fruit_drama.json` suggests `narrated_drama_60s_v2`. New-story form: a "Episode
  format" select pre-filled from the style, sent as `episode_template_id` (API: accept it on story creation and on
  `PATCH /stories/{id}` while no episode has a script).
- Tests: `tests/test_story_episode_schemas.py` (`EXPECTED_EPISODE_TEMPLATE_IDS` grows by two, on purpose);
  `tests/test_story_prompts_episode.py` ep-1 goldens unedited + a new narrated-ask case; `tests/test_aistory_templates.py`
  (seven ids and prose unchanged; the suggestion read); a wizard contract test; a store/API test for
  `episode_template_id`.
- Risk: RC-M1 (gate the ask on the field), the seven-style pins. Rollback: revert.

### Stage 2 — plot archetypes for the season arc, four more concepts
- `templates/archetypes/*.json`: infidelity, inheritance, betrayal, forgiveness, secret_child, rigged_contest,
  reality_show_parody — each `{id, label{en,fr}, premise, beats[6] (one per ARC_FUNCTION), twists[3], payoff}`.
  `prompts.build_s1` (v2 stories only): the pick-list in the ask; `season.json` entries gain optional `archetype`;
  the knowledge step and series memory carry it (read-only mention in the slices). Four new concepts
  (`concept_v1`): infidelity, betrayal, forgiveness, secret child, in the fruit world, FR/EN.
- Tests: `tests/test_aistory_templates.py::test_exactly_the_ten_shipped_concept_ids` → fourteen (on purpose); an
  archetype schema test; the S1 ask carries the list only on v2 (verify first whether an S1 golden exists — the
  explorer found none).
- Risk: low (no S1 goldens found; confirm). Rollback: revert.

### Stage 3 — the call to action
- `steps/metadata.py` `PART_CALL` → the CTA wording above (three pinned strings in `tests/test_story_metadata_step.py`
  change in lockstep). `render/subtitles.py end_card_ass`: an optional second line with the CTA when the template
  says `end_card_cta`; `render/plan.py` passes the flag; the golden fixture (literal TEMPLATE) stays off.
- Tests: metadata strings; a new end-card test with the flag on and off (ASS text), `tests/test_aistory_render_golden.py`
  unedited.
- Risk: RC-M2 if `filtergraph.py` shared argv changes — keep the logic in `subtitles.py`. Rollback: revert.

## Verification
Tier 1 per stage in both envs (DEC-234); the full local suite once before the deploy of task A + B together.
Tier 2: a new Fruit Drama story on the narrated template, episode 1 on the fast track (≈ $2.6), the human's phone
verdict ("a telenovela a first-time viewer follows; the narrator carries it; 2–4 lines lip-synced").

## Rejected
- Forcing v2 + narrator by style (the human's rule is a per-story switch; a style is a look).
- Shipping BGM without a licence record (DEC on the BGM licence stands).

## DECISIONS check
DEC-231 (narrator on: extended by a share), DEC-129/241 (episode templates per story: the field now read),
DEC-219 (quality: unchanged), RC-M1/M2/M3 guarded as above. No conflict.

## Riskiest stage
Stage 1 (the template selection path touches the story document, the schema const and the wizard).
