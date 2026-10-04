# 18 — Competitive analysis: rzdhop AI Story vs TrendStory, the Kings-Fruits formula and the 2026 AI-story apps

Date: 2026-10-04. Sources: three web-research sweeps run this session (TrendStory product pages and
revenue trackers; the TikTok `#KingsFruit` cluster, the Kings-Fruits Skool course and the fruit-drama
press; 30 AI story/video apps and current fal/Google API prices) plus this repository's own state
(VISION.md, CHECKPOINT.md, DEC-219…262, docs/AI_STORY.md). Facts the sweeps could not verify are
marked *(unverified)*.

---

## 1. Where our version stands today (main `fb0bb38`, deployed)

**What it is.** A self-hosted, open-source, step-gated studio that turns a concept into a persistent
story workspace and produces ~60–75 s serialized vertical episodes with a consistent cast, a voice per
character, lip-synced clips, karaoke-style captions and a platform metadata pack. Phone-first web
dashboard, no sign-in, CPU-only VPS; every paid call shown as an estimate, capped per episode / day /
story, booked in a ledger that matches fal's own bill.

**The v2 quality pipeline (phase 7, DEC-220…262).**
- Pre-production: concepts (10 FR/EN templates incl. `tentafruit_island`) → bible → style lock (7 templates
  incl. `fruit_drama`, `family_3d`) → cast with dossiers, structured looks, sheets and pinned voices → places
  and props → 8-episode season arc → **knowledge base** (dossiers, world bible, props registry, season
  timeline, who-knows-what-when) → **continuity ledger** across episodes.
- Episode: script E1–E4 (beat sheet, one call per scene, hook/cliffhanger/teaser, consistency check with
  severities) judged by **J1 (first-time viewer)** and repaired; storyboard T1 v2 (6–18 beat shots, two-beat
  rhythm, varied camera); keyframes on Seedream 4.5 edit with up to 10 references, judged by **J2** and
  redrawn automatically (≤ 2 per shot, ≤ $0.40); every shot a Seedance I2V clip asking for a *performance*;
  Kling lip-sync on every speaking shot; Gemini voices measured, tail-guarded and faded; self-made SFX,
  shipped BGM; pure-FFmpeg render (1080×1920, loudnorm, word-pop or two-line captions, AI label, end card);
  metadata pack (TikTok / Shorts / Reels, EN fields for a FR story).
- Post: series memory, audience-feedback digest steering the next opening, proposed characters and twists,
  text-only line edit with partial re-render, re-voice, per-shot regenerate; a one-click fast track per episode.

**Cost per episode on fal (measured on `d0ee5ebd745d`, ep 2):** 15 keyframes $0.60 + redraws ≤ $0.40 +
15 clips / 73–86 s on seedance-1-pro-fast 720p ≈ $1.6–1.9 + 13 lip-syncs $0.27 → **≈ $2.6–3.2 an episode**,
story setup (sheets, plates, props) ≈ $1–2 once. 1080p adds ≈ $2.3 an episode.

**Live today:** episode 2 of *Fruit Business : Guerre Cœur* was resumed this session after the fal top-up
(job `2307df1b6b29`); the human's verdict on A-133…A-140 is still pending.

---

## 2. TrendStory (trendstory.io) — what it is and how we differ

| | TrendStory | rzdhop AI Story |
|---|---|---|
| Company | TRENDSTORY LLC (Wyoming), founded Apr 2026, ex-"FruitDrama" *(secondary source)*; claims 750k users, ~$80–138k MRR, ~2,500 subs | open source, self-hosted, one deployment |
| Price | $19.99 / $39.99 / $99.99 / up to $279.99 a month + top-ups; Pro ≈ 15 one-minute videos (**≈ $2.67 a video**, floor $40) | API cost only, **≈ $2.6–3.2 an episode**, no floor, caps |
| Entry modes | **AI Agent** (one chat prompt → finished video), **Express** (theme + one sentence), **Series**, **Studio** (per-scene image / motion / model / duration) | one gated path with approvals; a fast track only *per episode* |
| Turnaround | "5 minutes" | ≈ 10 min of clips + the approvals; a new story needs ~8 steps before episode 1 |
| Video models | 9: Veo 3.1, Seedance 2 / 2.5, Grok Imagine… ; images Nano Banana | Seedance 1 pro fast (default), Veo 3.1 lite (opt-in), Kling 2.5, LTX-2.3; Seedream 4.5 |
| Consistency | described / photo-uploaded / preset characters, "same face every scene and episode"; a character library reusable across series | sheets + 10-reference edits + J2 redraws + knowledge base + continuity ledger; design-reference upload; **no cross-story library** |
| Series memory | "episode ideas based on what has been told" | full series memory, feedback digest, proposals, twists rewriting the arc |
| Themes | 7 visual universes: **Fruit**, Anime, Horror, Claymation, Animals, Realistic, Objects (rotating "as trends do") | 7 style templates incl. Fruit Drama, 10 concepts; no Horror / Animals / Objects |
| Voices / lip-sync | provider undisclosed; no lip-sync named | a pinned voice per character, Kling lip-sync on every speaking shot |
| QA | none described | J1 / J2 / E4 judges with automatic repairs and redraws |
| Captions | karaoke / simple / italic animated styles | word_pop / two_line / none |
| Publishing, trends, analytics | none (download only); "trend" = the theme catalog | none |
| Reviews | none yet (too new) | — |

**Where we are ahead:** depth of consistency (knowledge base, ledger, judges), per-character voices and
lip-sync, cost transparency and caps, editability after generation (partial re-render), metadata pack,
no subscription, full control of the pipeline.

**Where TrendStory is ahead:** time-to-first-video (one prompt, five minutes), model breadth (Seedance 2.x,
Veo 3.1 standard, Grok Imagine), a reusable character library across series, horror / animals / objects
universes, a mobile-grade onboarding, and a growth engine (showcase accounts with 640k–3.4M followers).

---

## 3. The Kings-Fruits / fruit-drama formula — what the content requires

**Identification.** "Kings-Fruits" is (a) a French TikTok hashtag cluster (`#KingsFruit`, creators Le
Dénicheur Officiel, fruitela, Dramatik Fruit…) of fruit-kingdom mini-series, and (b) a $39/mo Skool course
"Kings-Fruits" (Presk Drama / Sami Ammour, 157 members) teaching fruit-drama production with Claude Code
plus image / video tools. The genre's peak: *Fruit Love Island* (@ai.cinema021, 3.3M followers in 9 days,
300M+ views, 28 episodes, Mar 2026) and the French *L'Île de la Skibidi Tentafruit* (~3M followers).

**The formula, item by item, against our pipeline:**

| Formula element | Fruit drama does | We do today | Gap |
|---|---|---|---|
| Length / shape | 1–2 min, 4–6 scenes of 3–5 s clips, recap → tension → peak → cliffhanger | 55–75 s window, 6–18 beat shots of 3–12 s, hook → body → cliffhanger → teaser | add a **recap beat** for ep ≥ 2 and a 90 s v2 template |
| Cast | 4–6 pun-named recurring fruit heads, a king, guards, family | 4 characters with dossiers; Fruit Drama style; `tentafruit_island` concept | add kingdom / family archetypes to the concept library |
| Plot engine | infidelity, pregnancy, inheritance, betrayal, forgiveness, reality-show parody; daily drops | LLM-written arc from the bible; no archetype library | **plot-archetype library** feeding the season arc |
| Narration | one dramatic telenovela **narrator** carries the story; few character lines | narrator on, but dialogue-heavy with a voice per character and lip-sync on each | a **narrated-drama episode template** (narrator 70 %, 2–4 lines, lip-sync only on those) |
| Visuals | 3D photoreal fruit heads, cinematic, every shot animated | `fruit_drama` style, every shot a Seedance clip, J2-checked keyframes | parity; verify fruit-face lip-sync (A-140) |
| Captions | non-negotiable, big, synced; viewers watch muted | word_pop karaoke, two_line | parity |
| Retention copy | "comment PART 2", Épisode N / Partie N, freeze-frame cliffhanger with teaser text | hook, cliffhanger, teaser line, end card | **PART N overlay**, CTA line in the end card and in the metadata caption |
| Music | telenovela piano / thriller cues at 15–20 % | shipped BGM by dominant emotion, ducked | add a telenovela BGM set |
| Cadence | one episode a day | manual | a **cadence planner** (see U7) |

---

## 4. The 2026 landscape in one page

**Table stakes now (every serious app):** 1–7 reference images lock a character; native audio in the video
model (Veo 3.1, Seedance 2.x, Sora); karaoke captions on export; credit metering; multi-model choice;
9:16 native; an LLM writes the script; per-shot regeneration; a persistent asset / element library; an API.

**Where leaders differentiate:** true serialization with a world bible (only Showrunner, arguably Saga —
**and us**); multi-reference consistency (Kling Elements, Runway Gen-4 lock, Seedance 2.x 12 refs); model-
level lip-sync (Kling, Hedra); long clips and scene extension (Veo 3.1 to 140 s); avatar durability (HeyGen);
publishing automation (AutoShorts, Faceless.video); price aggression (ByteDance, MiniMax).

**Open source:** Wan 2.2, HunyuanVideo 1.5, LTX-2.3 generate; LatentSync / Hallo lip-sync; ComfyUI,
MoneyPrinterTurbo, ShortGPT orchestrate. **None keeps a cross-episode world bible or character bank** — that
is our open-source moat, and it is unpublished as such.

**Prices that matter to us (Oct 2026):** Seedance 1 pro fast $0.022/s (ours) vs Seedance 2.5 intro $0.035/s
(480–720p, native audio, 12 references) *(promo, unverified duration)*; Kling 2.5 turbo pro $0.07/s;
Veo 3.1 lite $0.05/s, fast $0.10–0.12/s, standard $0.40/s; Hailuo 02 std $0.045/s; LTX-2.3 $0.08/s;
Kling lip-sync ≈ $0.014/s; Nano Banana $0.039/image; Seedream 4 $0.018–0.03/image; FLUX.2 $0.014–0.07/image;
ElevenLabs $0.05–0.10 / 1k chars; Fish Audio ≈ $0.015 / 1k chars.

---

## 5. Proposed upgrades (ordered; each is its own FULL task with a plan and the human's go)

### P0 — robustness found today (small)
- **U1 — a journaled request purged by fal is re-submitted, not failed.** On the resume, shot sh02's request
  from the locked run returned HTTP 404 `NOT_FOUND`; the step failed the shot and kept the $0.04 booking.
  Rule: a journaled request whose status is 404 is released from the ledger and submitted again once. Files:
  `clipping/aistory/steps/assets.py` (the journal replay), `clipping/providers/fal*.py`. Test: a fake queue
  answering 404 on status.
- **U2 — automatic fallback on a locked fal account.** On 403 `TOP_UP`, switch the episode's image and video
  links to the Gemini rungs when `GEMINI_PAID_API_KEY` is set (today the switch is an offer the human must
  accept). CHECKPOINT already names this as "a small rule the human may ask for".

### P1 — close the TrendStory gap (1–2 weeks each)
- **U3 — Agent mode: one prompt → episode 1 rendered.** A `story-fast-track` step that runs concepts → bible →
  style → cast → places → season → knowledge → episode 1 with the server's defaults, auto-approving every
  gate that J1 / J2 pass, stopping only on a refusal or a cap, under one shown total estimate. Studio mode
  (today's gates) stays the default; Agent mode is a labelled choice on the new-story form. This is the
  single biggest difference in time-to-first-video.
- **U4 — the fruit-drama pack.** (a) a `narrated_drama_60s_v2` episode template: narrator-led, 2–4 character
  lines, a recap beat on ep ≥ 2, a freeze-frame cliffhanger with teaser text, lip-sync only on the spoken
  lines (≈ −$0.20 an episode); (b) a plot-archetype library (infidelity, inheritance, betrayal, forgiveness,
  secret child, rigged contest, reality-show parody) the season step picks from and the knowledge base
  tracks; (c) "PART N" overlay and a "comment PART 2" CTA in the end card and the metadata captions;
  (d) 3 kingdom / family concepts and a telenovela BGM set with its licence record; (e) a 90 s v2 template.
- **U5 — model upgrade rungs with their prices.** Add Seedance 2.x (multi-reference + native audio) and
  Kling 3.0 Elements as selectable quality rungs beside seedance-1-pro-fast, priced in `pricing.py` from
  fal's pages, with one A/B episode walked and judged on the phone. Expected: fewer J2 redraws (references
  go into the clip itself) and dialogue-grade native audio as a Tier-3 option.
- **U6 — a cross-story character library.** Reuse an approved character (sheets, look, dossier, voice) in
  a new story; "photo → character" through the existing design-reference upload. Export / import bundle
  (already a planned follow-up) is the storage format.

### P2 — distribution, where every competitor is weak
- **U7 — publish and schedule.** TikTok Content Posting API and YouTube Data API keys in Settings (no app
  auth — the keys are the user's own), a cadence planner (one episode a day at a chosen hour), the metadata
  pack posted with the render, a per-episode "posted" record. Fits the no-auth rule: nothing is added to
  the app's own access.
- **U8 — trend intake and performance loop.** A `trend_brief` input on the concept step (pasted trending
  captions / hashtags, or a TikTok discover page the user pastes) steering the concept writer; pasted view /
  retention numbers per episode flowing into the existing feedback digest, so the next opening is written
  against what performed.

### P3 — the moat
- **U9 — publish the differentiator.** README / docs section "a story that knows itself": the knowledge
  base, the ledger, the judges, the cost caps — none of the open-source projects has it.
- **U10 — Horror, Animals, Objects universes** as style templates (TrendStory's catalog parity), each with its
  own font and BGM set.
- **U11 — per-character LoRA** (already in "Not yet"): price fal's training first; only if Seedance 2.x
  references (U5) prove insufficient.

### Rejected
- A hosted SaaS / credits layer: against the open, no-auth, self-hosted vision (DEC-173, RC-D1).
- Free image links for cast / places / props to cut cost: DEC-219 forbids it.

---

## 6. Open questions for the human (CLARIFY of the next task)
1. Which first: U3 (Agent mode) or U4 (the fruit-drama pack)? They are independent.
2. U4(a): narrator-led by default for the Fruit Drama style, or a per-story switch?
3. U5: is a Seedance 2.5 A/B episode (≈ +$1–2 over seedance-1-pro-fast) worth running now?
4. U7: TikTok Content Posting API needs a registered developer app; is that acceptable for this deployment?
