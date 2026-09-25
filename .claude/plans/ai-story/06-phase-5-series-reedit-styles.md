# Phase 5 prompt — series memory, audience steering, per-scene re-edit, remaining styles, export

Paste this into Claude Code from the repository root. Phase 4 (MVP) merged and acknowledged.

---

Continue under the repository protocol: read `.claude/VISION.md`, `.claude/DECISIONS.md`,
`.claude/ASSUMPTIONS.md`, `.claude/CHECKPOINT.md`, then
`.claude/plans/ai-story/00-MASTER-SPEC.md` in full. Follow spec section 0.

## Goal of this phase

Make the mode serial and editable.

1. **Series memory** (spec 2.6, step 13): after an episode is approved, prompt **S3**
   produces `recaps[epNN]` (≤ 40 words), open hooks and relationship-state deltas;
   `season.json` updated (pure merge function, tested). The next episode's **E1** receives
   the recap + open hooks, its **E3** must pay off at least one open hook (E4 checks it),
   and the recap line (≤ 3 s) is scheduled by the timing engine.
2. **Audience-feedback steering**: `POST /episodes/{ep}/feedback` stores pasted comments/
   stats and runs step `feedback`; prompt **F1** digests them (≤ 60 words + 3 directions);
   the digest is part of the next E1 context pack (labelled "audience" in the UI; the
   user picks which direction, or none).
3. **New characters and twists** (prompt **N1**): before episode N+1, propose ≤ 2 new
   characters and ≤ 2 twists consistent with the arc; accepted characters go through the
   phase-2 cast flow (K1 → sheets → voice) and are recorded in `series_memory.introduced`;
   accepted twists amend `season.json` (arc entry `summary` + `open_hooks_out`), with the
   previous text kept in a `history` list.
4. **Per-scene re-edit** (spec 6.6): from `EpisodeStudio`, change a line's text → re-TTS
   that line only → re-time → mark affected shots; regenerate a shot image / voice line
   with a note; swap camera motion / framing; change a transition; then **partial
   re-render**: only shots whose cache key changed are rebuilt, the sequence and audio
   graph are re-assembled, the manifest records what was reused. Endpoint
   `/steps/rerender` with `{ep}`; UI shows "3 of 11 shots re-rendered".
5. **Remaining styles exercised**: `anime`, `cinematic_real`, `cartoon_flat`,
   `storybook_watercolor`, `claymation` — each gets one Tier-2 episode (short cast of 2,
   fast-track on the free route) and any prompt/motion/typography fix goes into the
   template JSON (`version` bumped; existing `style_lock.json` files are unaffected — a
   test proves a lock never changes when its template does).
6. **Export / import bundle — OPTIONAL** (not a user requirement; do it last and only if
   the phase budget allows, otherwise record it as a deliberate follow-up): zip of the
   story folder without `episodes/*/assets/shots/*.mp4` unless requested; import validates
   every `$schema`, assigns a new `story_id`, rewrites paths; a round-trip test.
7. **Endpoints**: `/steps/memory`, `/steps/feedback`, `/steps/propose-next` (N1),
   `/episodes/{ep}/feedback`, `/steps/rerender`; optional `/export`, `/import`.
8. **Dashboard**: `SeasonBoard` series-memory panel live (recaps, open hooks,
   relationship graph as a simple list), feedback box with digest, "Propose next episode"
   cards (accept/reject each character/twist); `EpisodeStudio` re-edit controls and the
   partial re-render summary. Phone layout checked.
9. **CLI**: `--ai-story step <id> memory|feedback|propose-next|rerender --ep N`; optional
   `--ai-story export <id>`, `--ai-story import <zip>`.
10. **Docs + decisions**: `docs/AI_STORY.md` (series, re-edit), DEC for "series memory is
    the only carrier of continuity between episodes", A-entries per style from the
    Tier-2 runs (what drifted, what held); bundle format DEC only if the bundle shipped.

## Out of scope

Tier 2/3 video, reference-video import, uploads to platforms.

## Before you plan

Ask only what remains open. Propose stages (riskiest: partial re-render correctness),
regression contract (phases 0–4 incl. the golden render and clip mode), expected DEC/A,
and the Tier-2 script: approve episode 1 of the FR story, generate memory, paste fake
audience comments, accept one proposed twist and one new character (with sheets and
voice), write and render episode 2 with recap + payoff, re-edit two lines and one shot
image in episode 2 and confirm the partial re-render count; render one short episode per
remaining style; (if shipped) export and re-import the story.

## Acceptance

- Tier-1 green in both envs; new tests shown failing pre-change (S3/F1/N1 golden
  strings, memory merge, hook payoff check in E4, cache-driven partial re-render
  selection, lock-vs-template immutability; bundle round-trip only if shipped).
- Episode 2 opens with the recap and pays off an open hook; partial re-render rebuilt only
  the changed shots (manifest proves it).
