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
a tailnet with no token. Auth is opt-in (DEC-173): a token is asked only when
`API_TOKEN` is set, and the two public paths (a Caddy domain, the Kaggle ngrok
tunnel) never start open. AI Story runs on **free** hosted tiers by
default, on a local GPU when one is detected, and on paid APIs only by explicit
opt-in with caps (default ceiling $1 per episode).

## The shift this work serves
Clips was decoupled into a **local-first** tool: external tools acquire the
`.mp4` and `.vtt`; the engine ingests local paths, skips Whisper when a
transcript is supplied, analyses with a chain of hosted LLM providers, and
renders through FFmpeg/OpenCV. AI Story reuses that chain system and extends it
to images, image editing, video, TTS and vision, with every paid call gated by a
budget and every free call counted against its daily limit.

## Where it stands (2026-09-30)
- **Auth is opt-in** (DEC-173, done 2026-09-29): no sign-in unless `API_TOKEN` is set, as the human asked
  ("remove all access restrictions to the app"); the VPS runs open on its tailnet with no override, the public paths
  refuse to run open, and an open API refuses other websites' writes. Acknowledged by the human on the phone.
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
- **The paid-path test** on fal.ai is **done** ($0.6934 of a $3 hard ceiling, DEC-174): every refusal and cap shown
  with its numbers before anything ran, a real paid assets run (35 requests: shots and reference edits), a forced
  poll failure resumed without re-buying, a same-input regenerate served from the gencache at $0. The ledger
  matched fal's own dashboard ($0.70) once the per-megapixel price was fixed to round up whole megapixels, the way
  fal actually bills (DEC-175). `allow_paid` went back off afterwards; both throwaway test stories are kept.
- **AI Story phase 5** (step 13 + re-edit) is **done**: series memory as a fold over each approved episode's own
  entry (a recap, hooks opened/closed, relationship deltas), gating episode N+1's script, storyboard and the fast
  track on the previous episode's memory being approved and fresh; pasted audience feedback (capped at 6,000
  characters, never trimmed) digested into three directions, the chosen one steering only the next episode's
  opening; new characters and twists proposed between episodes and decided one at a time, a twist rewriting the
  arc entry with the old text kept; a text-only line edit that now keeps the storyboard approval and re-times in
  place instead of forcing a full re-plan, a re-voice that persists its note, and a shot-image regenerate — each
  re-rendered *only* for the shots that actually changed (the partial re-render, proven equal to a full render
  under real ffmpeg); shot timing quantized to whole frames so an edit's render cache keys survive it; five more
  style templates each with their own OFL/Apache-2.0 font. Walked live on the FR story for $0 end to end (stage 13,
  acknowledged by the human on the phone). Stage 14 rendered one episode per remaining style on the free route
  (anime 52.7 s, cinematic_real 41.3 s, cartoon_flat 56.9 s, claymation 45.2 s, storybook_watercolor 48.0 s; every
  font from the shipped file, −14.1…−14.5 LUFS) and fixed four live bugs on the way (DEC-193); the capped paid
  re-edit test spent $0.03 on fal and its re-render matched the dry run (DEC-194).

## Next, in order
1. **Phase 6 close** — Tier 2 (I2V clips) and Tier 3 (native audio, opt-in per shot) now exist, gated by each
   story's own tier/route, behind the same `allow_paid` switch and per-episode/daily/per-story caps as every other
   paid call. Video runs hosted (fal seedance/ltx-2.3/kling, Gemini Veo) or on a local ComfyUI; local video is
   built but **unverified live** — no GPU exists on this deployment, so it has only run against a fake server.
   One image provider and one video provider now stick per episode, offered a switch rather than silently mixed.
   Paid LLM calls are estimated, capped and booked too (OpenRouter itself stays unfunded by choice). Remaining
   before close: the live walk's billing comparison, the human's final phone watch, and the merge to `main`.
2. **Phase 7 — quality overhaul** (DEC-219, the human's verdict after phase 6's phone watch): every shot animated,
   quality image models for characters, places and props (never cheap or free image AI), billed APIs strongly
   recommended when the host cannot run good models (this VPS has no GPU), prompts with real context, and an
   episode a first-time viewer can follow. Brief: `.claude/plans/ai-story/15-phase-7-quality-overhaul.md`.
   Reference-video import (the old phase 7) is off the schedule, kept for later.
Carried alongside: the Settings per-task route selector (DEC-112); the clip-upload token-before-spool fix; a dependency pass
(extras, lockfile, audit, setuptools ≥ 83); `jobs.json` atomic write and
`needs_upload` at restart; tests isolated from a real `.env`; phase 3's own
follow-ups (a garbled French accent with no code fix, storyboard/script
re-timing after an edit, T1 occasionally under-shooting a scene's shot
count — see `.claude/CHECKPOINT.md`); phase 4's own follow-ups (free-tier image quality on pollinations,
a single HTTP 500 ending the paced rounds, short English scripts, the BGM licence record — see
`.claude/CHECKPOINT.md`); phase 5's own follow-ups (the export/import bundle, a deliberate follow-up rather than
an oversight; the places step has no free-tier pacing yet, unlike cast and assets; a CLI switch to use the stored
Settings instead of the environment/`.env` only; E4's occasional payoff-variance false negative, A-084; French
scripts running short, 41–48 s; one image provider per episode, since mixing breaks the look, A-087; the voice picker
avoiding Gemini TTS on the free route, 10 requests a day, A-091; Hugging Face TTS as a candidate voice link).
