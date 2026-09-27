# VISION — rzdhop AI (a fork of OpenSource Clipping)

## Purpose
An open-source content factory with two modes behind one backend, one worker,
one dashboard, one auth, one settings store and one deploy:

- **Clips** — long-form video (podcasts, talks, streams) into vertical short-form
  clips with burned-in karaoke subtitles, face-tracked framing, B-roll, BGM and
  platform-ready metadata.
- **AI Story** — a gated, step-by-step studio that turns a concept into a
  persistent story workspace (world, style lock, characters, places, props, season
  arc) and produces ~60-second serialized episodes with consistent AI characters,
  per-character voices and a metadata pack. Its value is consistency +
  serialization + retention structure, not "one prompt → video".

## Business context
Published open-source, used through Colab/Kaggle notebooks and the web
dashboard, mostly from a phone. Users do not control the machine it runs on and
often have no working CUDA stack; this deployment is a CPU-only VPS reached over
a tailnet with no token (DEC-105). AI Story runs on **free** hosted tiers by
default, on a local GPU when one is detected, and on paid APIs only by explicit
opt-in with caps (default ceiling $1 per episode).

## The shift this work serves
Clips was decoupled into a **local-first** tool: external tools acquire the
`.mp4` and `.vtt`; the engine ingests local paths, skips Whisper when a
transcript is supplied, analyses with a chain of hosted LLM providers, and
renders through FFmpeg/OpenCV. AI Story reuses that chain system and extends it
to images, image editing, video, TTS and vision, with every paid call gated by a
budget and every free call counted against its daily limit.

## Where it stands (2026-09-27)
- **Clips**: stable; the render layer may change only with frame-parity proof.
- **AI Story phase 0** (foundation): product renamed rzdhop AI, two-mode shell,
  generation chains and adapters, pricing, free-tier limiters, budget with caps
  and a cost ledger, hardware profiler, four-tab Settings. Its one paid Tier-2
  call is deferred by the human (no budget).
- **AI Story phase 1** (steps 1–4): story store and index, story-step jobs that
  wait for approval and survive a restart, the concept library (10, FR/EN) and
  "Generate 10 more", the bible (three calls, regenerate a field with a note),
  seven style templates and the style lock with a free three-image preview, the
  stories API, the `--ai-story` CLI, and the dashboard wizard. Verified live on
  the free chain at 375 px (DEC-116). Story steps never call a paid LLM link
  without `allow_paid` (DEC-115).
- **AI Story phase 2** (steps 5–7): characters picked from the sketch with K1 text, portrait, turnaround and
  expression sheets, a pinned voice and a sample; design-reference uploads described for K1; places and props
  proposed then edited, master plates and time variants; an 8-episode season arc; per-entity approvals up to
  `ready`. Without a reference-capable editor the sheets stop and ask, and prompt-only consistency is the
  user's labelled choice (DEC-117). Walked live on the free route for $0.00 (DEC-125).

## Next, in order
1. **Phase 3** — episode writer: script (E1–E4), storyboard (T1), timing.
2. **Phase 4** — assets, Tier-1 renderer, metadata pack (**MVP**), with the
   generation cache that never loses a paid generation (DEC-106 follow-up).
3. **Phase 5** — series memory, audience steering, per-scene re-edit, remaining styles.
4. **Phase 6** — Tier 2/3 video, local ComfyUI workflows, paid estimates end to end.
5. **Phase 7** — reference-video import.
Carried alongside: the Settings per-task route selector (DEC-112); the clip-upload token-before-spool fix; the deferred paid Tier-2 step of phase 0; a dependency pass
(extras, lockfile, audit, setuptools ≥ 83); `jobs.json` atomic write and
`needs_upload` at restart; tests isolated from a real `.env`.
