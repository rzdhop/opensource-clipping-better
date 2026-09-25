# AI Story mode — planning bundle for Claude Code

Written 2026-09-25 from a question-and-answer session with the author, then reviewed
(40 findings fixed in v1.1). Lives at `.claude/plans/ai-story/` in the repository.

| File | Use |
|---|---|
| `00-MASTER-SPEC.md` | The specification. Every phase prompt points to its sections. Read fully before any phase. |
| `01-phase-0-rename-shell-providers.md` | Paste into Claude Code first: rename to rzdhop AI, two-mode shell, generation chains, hardware profiler, budget. |
| `02-phase-1-workspace-concepts-bible-style.md` | Story store, concept library, bible, style lock. |
| `03-phase-2-cast-places-season.md` | Characters (sheets + voices), places, props, season arc. |
| `04-phase-3-episode-writer.md` | Script, storyboard, timing, consistency check. |
| `05-phase-4-assets-render-metadata.md` | Assets, Tier-1 renderer, metadata pack — **MVP**. |
| `06-phase-5-series-reedit-styles.md` | Series memory, audience steering, per-scene re-edit, remaining styles, export. |
| `07-phase-6-video-tiers-local.md` | Tier 2/3 video, local ComfyUI, paid opt-in. |
| `08-phase-7-reference-import.md` | Reference-video import (style / premise / archetypes). |
| `09-APPENDIX-research-2026-09-25.md` | Provider prices, free tiers, prior art, genre notes — the source for `pricing.py`. |
| `10-REFERENCE-ANALYSIS-2026-09-25.md` + `reference-v1/v2-contact-sheet.jpg` | Measured structure, pacing, captions, audio and style of the author's two inspiration videos; the "measured vs assumed" table that shaped §6.2 and the two MVP styles. |

How to run a phase: paste the phase file's content (below the `---`) into Claude Code at
the repo root. Each phase ends only after Tier-1 is green, Tier-2 is run live and you
have acknowledged it in chat, and `.claude/` (CHECKPOINT, DECISIONS, ASSUMPTIONS, VISION)
plus `docs/AI_STORY.md` are updated.

Decisions taken during the Q&A (so nobody re-asks them):

- Same app, two modes; one backend/worker/dashboard; new package `clipping/aistory/`.
- Visual tiers chosen per story: 1 = stills + motion, 2 = image-to-video per shot, 3 = with native model audio (experiment).
- Audio: per-character dialogue voices + BGM/SFX; narrator optional per story (off by default).
- Budget: free tiers by default; paid providers only by explicit opt-in with caps and estimates; **ceiling $1 per episode** (default `one_dollar` profile: all shots as reference-consistent images + key shots animated within the cap).
- Local generation: auto-detected hardware profile, recommendations, per-task route `auto|local|api`.
- Human approval gate at every step; everything regenerable with a note and lockable.
- Consistency locks: characters (sheets), places (master plates), style/palette/lighting, props.
- One language per story (FR or EN).
- Reference-video import extracts style + palette, premise (remix) and cast archetypes — not pacing templates.
- Seven shipped styles: fruit_drama, family_3d, anime, cinematic_real, cartoon_flat, storybook_watercolor, claymation.
- Concepts: 10 curated (bilingual) + "generate 10 more".
- Series: cliffhanger + recap, season arc planned upfront, new characters/twists per episode, audience-feedback steering.
- After render: metadata pack + per-scene re-edit; no auto-upload in v1; export/import bundle is optional (not requested).
- Consistency without a reference-capable image editor is an explicit per-story opt-in ("prompt-only"), never automatic.
- Episode: target 60 s (window 55–80, a 75–100 s template exists), 8–12 scenes of 4–8 s, 2–4 shots per scene; 9:16 only in v1.
- From the reference videos: fruit_drama uses single-word pop captions, a diegetic insert hook and a hard-stop cliffhanger; family_3d uses no captions, a shocking-image hook and long emotional holds; both keep a continuous music bed and an "AI-generated" label.
- MVP = bible → Tier-1 episode with 2 styles (phase 4).
