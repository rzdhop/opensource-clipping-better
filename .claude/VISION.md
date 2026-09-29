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

## Where it stands (2026-09-29)
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
- **AI Story phase 3** (steps 8–9) is **done**: episode 1's script (E1–E4: a beat sheet, one call per body
  scene, hook/cliffhanger/teaser, a consistency check) and storyboard (deterministic fast shots or T1, 2–4 per
  scene), timed in Python from a word-budgeted model reply, plus opt-in real-voice measurement kept as phase 4's
  line audio. Episode approvals live on the episode documents, never on the story (DEC-129); episode N ≥ 2 waits
  for phase 5's memory step (DEC-130). Live Tier-2 walked by me at 375 px, one round of majors fixed (episode
  length, storyboard timing against the episode-level window pass), **acknowledged by the human 2026-09-27**;
  merged to `main`, deployed and pushed with phase 4.
- **AI Story phase 4 — the MVP** (steps 10–12) is **done**: assets (shot images on the free image chain with
  paced retries for rate-limited free tiers, line audio in each speaker's pinned voice, self-made SFX, shipped BGM
  picked by the episode's dominant emotion), a pure-FFmpeg Tier-1 renderer (1080×1920, 30 fps, motion on stills,
  ducked music bed, loudnorm I −14 / TP −2.5, four subtitle modes, the AI label and end card; golden framemd5
  per ffmpeg build), the metadata pack (TikTok, Shorts, Reels; EN fields for a French story; the cover), a generation
  cache that never loses a paid generation, signed episode media, and a one-job fast track that stops before any
  paid spending. Tier-2 walked live for $0.00: a French episode step by step (58.2 s) and an English one through the
  fast track (48.8 s); both **accepted by the human 2026-09-29** ("Finish, update artefact, push then
  merge"); merged to `main` and pushed.

## Next, in order
1. **Phase 5** — series memory (episode 2 onward), audience steering, per-scene re-edit, remaining styles.
2. **Phase 6** — Tier 2/3 video, local ComfyUI workflows, paid estimates end to end (incl. the deferred live
   paid assets step, which settles whether fal bills a failed queued request, A-071).
3. **Phase 7** — reference-video import.
Carried alongside: the Settings per-task route selector (DEC-112); the clip-upload token-before-spool fix; the deferred paid Tier-2 step of phase 0; a dependency pass
(extras, lockfile, audit, setuptools ≥ 83); `jobs.json` atomic write and
`needs_upload` at restart; tests isolated from a real `.env`; phase 3's own
follow-ups (a garbled French accent with no code fix, storyboard/script
re-timing after an edit, T1 occasionally under-shooting a scene's shot
count — see `.claude/CHECKPOINT.md`); phase 4's own follow-ups (free-tier image quality on pollinations,
a single HTTP 500 ending the paced rounds, short English scripts, the BGM licence record — see
`.claude/CHECKPOINT.md`).
