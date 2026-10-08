# Plan 36 — AI Story rebuilt from scratch, Claude-native (draft for discussion)

Date: 2026-10-08. Status: ACCEPTED with the decisions of §7 — stage 0 DONE 2026-10-08 (D7: path a); next: stage 1. Numbered 36: plans 30–35 were done on the other system on 2026-10-06/07 (see 36-CHECKPOINT.md). Supersedes plans 00–35 for the AI Story mode once accepted.

## 0. The problem and the root causes

**Problem.** After 29 plans and ~75,000 lines in `clipping/aistory/`, no produced episode has been good enough to post. The pipeline has to be rebuilt around Claude as the writer/director inside the session, with the GPU as the only external call.

**Root causes (what the old design got wrong, from reading the code, the plans and the research):**

1. **The app was the writer.** Every creative step was an LLM API call behind a schema, a validator, a budget gate, a provider chain and a retry policy (`steps/llm_call.py`, `schemas.py` 6.4k lines, `prompts.py` 6.5k lines). The quality ceiling was the cheapest free model in the chain, and the machinery around it is most of the code. With Claude in the session, all of that is replaced by *writing*.
2. **Voices were stitched on.** edge-tts / chatterbox / lipsync passes (`voices.py`, `steps/lipsync.py`) produce robotic speech and glued mouths. The existing LTX-2 template even throws the model's own sound away (`i2v_ltx2.json`: "the clip is saved silent").
3. **Stills with motion were allowed** (tier-1 Ken Burns in `motion_rules`), so the budget logic kept falling back to non-video.
4. **Too many providers, too many formats, too many switches** (ten universes, seven styles, three tiers, 9:16/16:9/1:1, narrator/no narrator, agent mode/studio mode). Each switch multiplied prompts and tests, none of it improved one episode.
5. **No human gate per clip.** Approvals existed per step, not per clip, so a drifting clip went straight into the final render.

## 1. Reality checks from the research (these change the brief)

| Claim in the brief | What is true on 2026-10-08 | Consequence |
|---|---|---|
| "Wan 3.0 / 2.2 with native speech" | The official `Wan-AI` HF org tops out at **Wan 2.2** (silent) and **Wan2.2-S2V** (audio *in*). Wan 2.5 / 2.6 / 2.7 / "3.0" are **API-only**; the sites claiming open 2.7/3.0 weights are SEO sites. No open Wan model generates speech. | Wan cannot be the native-speech model. |
| "ComfyUI 5.10" | The endpoint runs **worker-comfyui 5.10.0** (Sep 2026) which pins **ComfyUI 0.34.0** (upstream is 0.39.1). | LTX-2.5 needs ComfyUI ≥ 0.32 → OK on the current image. MiniMax H3 needs ≥ 0.30 → OK. |
| "a speech-native model like LTX" | **LTX-2.5** (Lightricks, open weights 11 Aug 2026, 22B, joint audio+video, native ComfyUI core nodes, official I2V template, int8 fits a 48 GB L40S, free commercial use < $10M revenue). It generates dialogue from quoted lines in the prompt, 48 kHz, expressive. | **LTX-2.5 is the primary model.** |
| "voices defined in the prompt so the video produces them" | True for one clip. **Across clips the prompt alone does not reproduce a voice** — same prompt, different seed = different voice. Every open joint model has this limit. The solved form of this is a **per-character voice reference** (a 5–10 s WAV, part of the character sheet, like the images) given to the video model: LTX-2.3 **ID-LoRA** (keyframe + voice clip → one-pass talking video, face and voice matched, a native ComfyUI node) or LTX-2.5 **A2V** (the line's audio is an input of the generation; the model performs lips, breath, body; the audio is kept bit-exact). Lightricks said in Aug 2026 the ID/dubbing pipelines are being ported to 2.5. | The voice reference becomes an asset of the character sheet. No edge-tts, no post lipsync pass, no stitching: the performance is generated with the clip. The "prompt-only voice" path stays as the fast path for one-off characters. |
| "many characters can talk in one 5 s clip" | Open joint models are unreliable past one speaker per clip (faces merge, speaker assignment is luck). Speech budget is ≈ 2.5 words/s with a beat of silence at each end: **5 s ≈ 10 words (1 line), 8–10 s ≈ 20–25 words (2–3 lines)**. | Default: **one speaker per clip, shot / reverse-shot** (this is also what vertical telenovela coverage wants). Two-speaker exchanges only in 10 s clips, only through the audio-driven path (two voice tracks), to be validated in the test run. |
| MiniMax H3 (the closest "Veo-3-like" open model: 9 ref images + 3 voice refs, `<d>` dialogue tags) | Its license **excludes the EU** (and USA/UK/KR) unless MiniMax grants one; 5–10 min per clip. | Not in the chain unless you obtain the license. |
| worker-comfyui returns videos | The stock handler only harvests the `images` history key: core **SaveVideo → returned**; **VHS VideoCombine → silently dropped**. `/runsync` responses cap at ~20 MB. | Workflows end in core `CreateVideo → SaveVideo (h264 + AAC)`; set the S3/R2 env vars so clips come back as URLs. |
| "Gemini-quality voices" | Veo 3 / Gemini voice drift is also real for creators (noticeable by the 3rd–4th clip); their fix is one locked voice per character. | Same design, on our own GPU. |

## 2. Vision of the new pipeline

**One sentence:** Claude writes and directs in the session; the repo holds the story as files; the MCP server does three things only — GPU jobs, media assembly, and verification; every clip is seen and approved by Rida before the next step.

### 2.1 What exists after the rebuild

```
showrunner/                      # new package, replaces clipping/aistory (named 2026-10-08: any universe, D8)
  store.py          story folder layout, lock states, ids, cost ledger (small)
  prompts/          *.md prompt templates with {{placeholders}} (character sheet, turnaround,
                    emotion grid, keyframe, clip-dialogue, clip-reaction) — one file per template
  workflows/        ComfyUI API-format JSON + a patch table per template
  assemble.py       ffmpeg: concat clips, subtitles (ASS, Montserrat Black / Bebas), BGM ducked, SFX, end card
  verify.py         STT (whisper) on a clip's own audio aligned to the line; contact sheet of frames
  mcp_server.py     the rzdhop-story MCP v2 (tools in §4)
stories/<slug>/                  # the persistence (git-tracked, images as files)
  00-brief.md                    # what Rida asked, the chosen concept
  01-universe.md                 # world rules, proportions, species, period, palette, medium sentence
  02-cast/<char>/sheet.md        # identity block, master head prompt, wants, voice description
  02-cast/<char>/full_body.png · turnaround.png · emotions.png · voice_ref.wav
  03-places/<place>/plate.md + plate.png
  04-season.md                   # arc, episode list, cliffhanger shapes rotation
  ep01/script.md                 # beats + lines (the writer's artifact)
  ep01/shots.json                # the shot list: clip length, speaker, line, keyframe prompt, clip prompt
  ep01/keyframes/sNN.png · ep01/clips/sNN_vK.mp4 · ep01/takes.json (STT verdicts, approvals)
  ep01/final.mp4 · ep01/metadata.md (title, hook text, comment bait)
  memory.md                      # what happened, relationship graph, open threads (series memory)
.claude/skills/                  # the chain of skills Claude follows (one per step)
```

Deleted: `clipping/aistory/` (all steps, schemas, prompts, provider chains, judge, lipsync, voices, tiers), `clipping/story*`, the story pages of the dashboard (a read-only viewer can come back later), edge-tts, every Ken Burns path, every "tier", every non-9:16 format.

Kept (validated, copied over, not imported): the **medium sentences** (`_FRUIT_PEOPLE`, `_CGI` — the fix for the Veo "fruit mask on an actor" failure), the `fruit_drama.json` style template (rendering / camera / lighting / character_design_rules / negative), the ComfyUI templates `t2i_flux2_klein` and `edit_flux2_klein_multiref` (verified live), the speech-capacity math of `native_speech.py` (2.4 wps, overhead 0.7 s, STT-aligned take verdicts), the ASS subtitle/ducking/limiter recipe of `productions/faille_damour/render_ep01.py`, the fonts, `assets/bgm`, `assets/sfx`, the cost ledger idea.

### 2.2 The six steps, as Rida described them, with the upgrades

**Step 1 — Concepts (free).** Rida gives a pitch. Claude proposes **3 concepts**, each as a card: logline, the central secret, the cast in one line each, the *last 5 seconds of episode 1* (one image, one line, one open question — the cliffhanger is written first), the comment bait, why people come back. Virality lens (from the research): emotional clarity beats render quality; recurring cast with a relationship graph; one trope the audience already knows (cheating reveal, who's the father, inheritance, Casa-Amor-style arrival); daily cadence; "Part N+1" mechanics; a season map of ≥ 10 episodes sketched (hook block E1–3, reversal, all-is-lost, payoff). Saved to `00-brief.md`.

**Step 2 — Universe (free).** `01-universe.md`: medium sentence (fruit people / 3D cartoon / claymation / …), head types allowed (fruit, vegetable, crystal, soda can, animal…), head-to-body ratio, hands rule, period and setting, palette (hex), lighting, camera coverage rule, forbidden things, the *negative block*. Inspired by the existing style templates. This file is the first block of every image and clip prompt — the "style lock".

**Step 3 — Cast (GPU: images + one voice reference per character).** Per character: `sheet.md` with the **identity block** (species, ripeness, colours in hex, outfit, one permanent mark, one signature item, silhouette), wants/fears/secret, how they speak, and the **voice description** (age, tone, accent, texture). Images, in this order, each approved before the next:
1. `full_body.png` — Flux 2 Klein t2i, 832×1216, neutral grey, flat light. 3–5 candidates, Rida picks ONE canonical image; it is **locked** and never regenerated.
2. `turnaround.png` — front / three-quarter / side / back from the canonical image with the multi-reference editor (or Qwen-Image-Edit-2511 + the Multiple-Angles LoRA for deterministic azimuths: `front view / right side view / back view / front-left quarter view`).
3. `emotions.png` — 6-grid: joy, anger, doubt, love, thinking, sad, same lighting, same framing.
4. `voice_ref.wav` — 5–10 s, generated once from the voice description with a voice-design TTS (Qwen3-TTS VoiceDesign / Chatterbox / IndexTTS-2, all with ComfyUI nodes) or from a consented recording; **locked**. It is the character's voice for the whole series.
Then the **master head prompt**: a ~60-word block (species + face + colours + mark + signature item + outfit) that every keyframe and clip prompt starts with, plus the two references (`full_body.png`, one `turnaround` crop) attached to every keyframe job. Upgrade: a 3-clip **stress test** of the pack (three lightings, three emotions) before any episode; if identity drifts, rebuild the pack, never re-roll shots.

**Step 4 — Episode 1 script (free).** `ep01/script.md` written to the 90 s beat template below, cliffhanger first, ≤ 12 lines, 6–12 words per line, 1–3 speakers per shot (multi-speaker preferred, D7), one action per shot, hook readable with the sound off, no establishing shot, no title card. Claude self-checks: every line fits its clip's word budget; every character on screen has a sheet; every place has a plate; the cliffhanger shape rotates (revelation / reversal / deadline / intrusion).

| Time | Beat | Clips | Length |
|---|---|---|---|
| 0–5 s | Hook: mid-conflict, one sharpening line | 1 | 5 s |
| 5–15 s | Setup: what is at stake | 1–2 | 5 s |
| 15–55 s | Escalation: 3 beats, each raises the stakes | 4–5 | 8–10 s dialogue, 5 s reactions |
| 55–60 s | The turn: a choice | 1 | 5 s |
| 60–80 s | Peak: the shareable confrontation | 2 | 10 s |
| 80–90 s | Cliffhanger: cut before the reaction + sting + "Part 2" | 1 | 5 s |

≈ 11–13 clips, ≈ 10–12 lines, 90 s.

**Step 5 — Shots, keyframes, clips (GPU).** `shots.json`: per shot — duration (5 or 10 s, clamped to what the model sells: LTX frames = 8n+1 at 24 fps → 121 f = 5 s, 241 f = 10 s), characters, speaker, line, emotion from the 6-grid, framing, the keyframe prompt, the clip prompt.
- **Keyframe**: multi-reference edit (character refs + place plate), 736×1280 or 768×1344 (9:16, divisible by 32), 2–3 candidates per shot, Claude looks at them and proposes one, Rida validates.
- **Clip**: LTX-2.5 I2V from the keyframe, picture and voice in one pass, the lines quoted and each speaker's voice described in the prompt (path (a), D7; a locked voice reference applies only through the voice-conversion test). Prompt pattern: *[master head prompt] [what happens next, not a re-description of the image] says: "line." [delivery: emotion from the sheet, pace] [camera: still on the speaker during the line] Audio: [room tone], no music, no subtitles.* Reactions: 5 s, silent clip with ambience only.
- **Verification** before Rida sees it: STT of the clip's own audio aligned to the line (≥ 75 % words heard, last word ends ≥ 0.1 s before the end), a contact sheet of 8 frames, duration check. A failed take is regenerated with a new seed and a note; never filled with a still.
- **Rida's gate per clip** (the test-run requirement): approve / regenerate with note / change the line. Nothing is assembled from an unapproved clip.
- Continuity upgrade: the last frame of clip N is saved and offered as a reference for the keyframe of N+1 when the shot continues; a 1-line *end-frame description* is written per episode for episode N+1's hook.

**Step 6 — Assembly (free, ffmpeg).** Concat at 1080×1920 @ 24 or 30 fps, trim each clip after its last word + 0.3 s, burned subtitles from the verified STT timings (Montserrat Black 74 px, coloured speaker names, kept off faces), BGM bed at ≈ 15–20 % of the voice with sidechain ducking, SFX from `assets/sfx` in the pauses, sting on the cliffhanger, final frame held 1 s, "Part 2 tomorrow" card. Contact sheet + `volumedetect` checked, then the mp4 is sent in the chat for review. Metadata: title with series label and episode number, hook text overlay (2 variants to A/B), comment bait question.

### 2.3 Cost per episode (self-hosted, from the infra research)
Images: ≈ $0.005–0.01 each on a 5090/L40S (≈ 40 images per cast of 4 + 25 keyframes ≈ $0.50). Clips: LTX-2.5 distilled int8 ≈ 20–50 s per 5–10 s clip on a 5090/L40S ≈ **$0.02–0.05 per clip** + one cold start per burst (≈ $0.05–0.10). An episode of 12 clips with 2 seeds each ≈ **$1–2**, cast setup ≈ $0.50. (Hosted LTX API would be ≈ $2.3–3 per episode; RunPod public Wan 2.2 ≈ $4.5 and silent.)

## 3. Infrastructure decisions (the GHCR image)

1. Base: `FROM runpod/worker-comfyui:5.10.0-base` (ComfyUI 0.34.0 — enough for LTX-2.5; rebuild from the upstream Dockerfile with `COMFYUI_VERSION=0.39.1` only if a node needs it). Custom nodes minimal (import time dominates cold starts: 43 s of imports measured vs 27 s of work): `ComfyUI-LTXVideo` (A2V / IC-LoRA workflows), `TTS-Audio-Suite` (Chatterbox / Qwen3-TTS / IndexTTS-2 for the voice references), `ComfyUI-qwenmultiangle` (turnarounds), nothing else. Install with `comfy-node-install`, pip only through `/opt/venv/bin/pip`.
2. Weights on a **network volume ≥ 150 GB** in one datacenter (LTX-2.5 int8 stack ≈ 57 GB, Flux 2 Klein, Qwen-Image-Edit, the TTS models), not baked into the image (a 60 GB image kills pulls and FlashBoot). Own `extra_model_paths.yaml` copied in (the stock one does not map `diffusion_models`, `text_encoders`, `audio_encoders`). `Lightricks/LTX-2.5` is gated: HF token with gated-read when filling the volume.
3. **Two endpoints**: `images` (Flux 2 Klein / Qwen-Image-Edit, 5090 or L40S) and `video` (LTX-2.5, **L40S 48 GB** for int8 — the cost sweet spot; RTX PRO 6000 96 GB if we want bf16 + the spatial upscaler). Mixing image and video models on one endpoint thrashes the model cache.
4. Endpoint settings: executionTimeout 1200 s, idle timeout 120–300 s so a whole episode reuses one warm worker, FlashBoot on, max workers 3–5 for the clip fan-out, `COMFY_LOG_LEVEL=INFO`, S3/R2 env vars (`BUCKET_ENDPOINT_URL`, keys) so clips return as URLs.
5. Workflows end in core `CreateVideo(images, audio, fps) → SaveVideo(mp4, h264+AAC)` plus a `SaveImage` of the last frame. Inputs over `input.images[]` (any file type works: png, wav) under the 10 MB `/run` cap; beyond that, pre-upload to R2 and fetch in-graph.
6. `/run` + polling (never `/runsync` for video). Optional handler fork later for progress events and VHS outputs.

## 4. The MCP server v2 (rzdhop-story) — what it exposes

| Tool | Does | Replaces |
|---|---|---|
| `templates_list`, `comfy_submit(template, params, files, dest)`, `comfy_status`, `comfy_fetch` | GPU jobs, parallel, with the patch table per template | kept from v1 |
| `store_read(story, path)` / `store_write(story, path, content|file)` / `store_lock(story, asset)` | the story folder, lock states (draft → locked → committed) | `story_step_*`, approvals DB |
| `verify_take(clip, line, lang)` | STT + alignment verdict, duration, last-word end | `native_speech.evaluate_take` |
| `contact_sheet(video)` / `view_file` | frames for Claude to look at | kept |
| `tts_voice_ref(description|sample, text)` | makes/locks a character's voice reference | `voices.py` |
| `assemble_episode(story, ep, approved_only=true)` | ffmpeg assembly, subtitles from takes, BGM/SFX, end card; returns the mp4 + sheet + loudness | `render/*`, `steps/render.py` |
| `cost_ledger`, `runpod_health` | money and warm workers | kept |

No tool calls an LLM. No tool decides anything creative. One run per story at a time.

(Note: the `rzdhop-story` MCP returned a 502 in this session — to check before stage 0.)

## 5. Stages

| Stage | Technical description | Output / gate | GPU cost |
|---|---|---|---|
| **0 — Spike: pick the voice path (2–3 days)** | Build the `video` endpoint with LTX-2.5 int8 distilled; make 3 clips of one existing fruit character (keyframe from the current store) in three ways: (a) prompt-only dialogue in LTX-2.5 I2V, (b) TTS line (Chatterbox / Qwen3-TTS from a voice ref) → LTX-2.5 A2V, (c) LTX-2.3 ID-LoRA (keyframe + voice ref, one pass). Same line, 3 seeds each. Also one 10 s two-speaker test on (b). | Rida listens and watches; we choose the primary + fallback. Decision written in this plan. | ≈ $2–3 |
| **1 — Store and skeleton (2 days)** | `showrunner/` package: folder layout, ids, lock states, cost ledger, prompt templates as `.md`, workflows JSON + patch tables (t2i, multiref edit, turnaround LoRA, emotion grid, LTX-2.5 clip, TTS ref), `assemble.py`, `verify.py`. Tests on fakes. | `pytest` green; one story folder created by hand | $0 |
| **2 — MCP v2 (2 days)** | The tools of §4 on the existing server base; `story_step_*` removed; the image endpoint re-pointed; S3/R2 outputs. | Smoke: one image, one clip, one verify, one assemble from the chat | ≈ $0.50 |
| **3 — Skills (2 days)** | One SKILL.md per step: `story-concepts`, `story-universe`, `story-cast`, `story-script`, `story-shots`, `story-clips`, `story-assemble`, `story-next-episode`; each with its prompt template, its checklist, its gate question and its "what to show Rida". The old `story-director` / `fruit-drama-episode` skills retired. | Dry run of steps 1–4 on a new pitch with no GPU | $0 |
| **4 — Validation run, episode 1 (3–4 days, the test Rida asked for)** | A real story from a pitch: 3 concepts → universe → cast of 3–4 (every image and voice ref gated) → script → shots → every clip gated one by one → assembly → mp4 in the chat. Every defect logged in `stories/<slug>/ep01/defects.md`; prompts and templates fixed as we go; no drift allowed: a failed gate stops the run. | Episode 1 posted or declared not good enough, with the list of why | ≈ $3–5 (cast + 12 clips × ~2 seeds) |
| **5 — Delete the old world (1 day)** | `git rm` `clipping/aistory`, `clipping/story*`, story dashboard pages, their tests and fixtures, docs rewritten (`docs/AI_STORY.md`), CHANGELOG, VISION. | CI green; repo ~75k lines lighter | $0 |
| **6 — Episode 2 and series memory (2 days)** | `memory.md` written from ep 1 (what happened, relationship graph, open threads, the end-frame description), audience feedback pasted in, `story-next-episode` skill proposes 3 directions; continuity references (last frames). | Episode 2 through the same gates | ≈ $2 |

Stages 1–3 can run in parallel with stage 0's GPU waits. Each stage ends with a line in the action log and a checkpoint, as before.

## 6. Open questions for Rida (answers go in §7)

1. **Voice path** — decide after stage 0, or already commit to the voice-reference design (ID-LoRA / A2V) and keep prompt-only as the fast path?
2. **GPU** — L40S 48 GB (int8, cheapest per clip) or RTX PRO 6000 96 GB (bf16 + upscaler, ≈ 2× the price)?
3. **Persistence** — `stories/` in the git repo (images and wav as files, LFS if needed) or the Claude project docs, or both (repo = truth, project = mirror for sessions)?
4. **Dashboard** — no story UI at all in this rebuild (chat only), or a read-only viewer of the story folder later?
5. **Language of the episodes** — French, English, or per story? (LTX-2.5 dialogue is multilingual; the voice references must be made in the episode's language.)
6. **Two speakers in one clip** — only through the audio path and only in 10 s clips (my proposal), or never?

## 7. Decisions (Rida, 2026-10-08)

| # | Decision | Consequence in the plan |
|---|---|---|
| D1 | **Stage 0 tests all three voice paths** (prompt-only LTX-2.5 I2V, TTS→LTX-2.5 A2V, LTX-2.3 ID-LoRA) and the primary is chosen after listening. | Stage 0 matrix stays 3 paths × 3 seeds on the same line and keyframe; the decision is written here as D7 after the spike. |
| D2 | **Video endpoint = L40S 48 GB, LTX-2.5 int8 distilled.** | Dockerfile and volume sized for the int8 stack (≈ 57 GB); no bf16 / spatial upscaler in v1. |
| D3 | **Persistence = `stories/` in the git repo**; images and wav as files (LFS if the repo grows). | No project-doc mirror; sessions read the repo through the linked computer. |
| D4 | **Episodes in French or English, chosen per story.** | `00-brief.md` carries `language`; the voice references are generated in the story's language; prompts to the image/video models stay in English. |
| D5 | **No story dashboard** in this rebuild — chat only. | Stage 5 removes the story pages; the web UI keeps Clips. |
| D6 | **1, 2 or 3 speakers per clip, no hard limit.** Honest caveat from the research: every open joint model is unreliable past one speaker (faces merge, speaker assignment is random); the audio-driven path with one voice track per speaker is the only one with a documented multi-speaker mode (InfiniteTalk-multi, LongCat-Avatar-1.5, SkyReels-V3-A2V; LTX-2.5 A2V does not bind a voice to a face). | Stage 0 adds a **2-speaker and a 3-speaker 10 s test** on each path. The writer may plan multi-speaker clips; the shot planner marks them `multi_speaker: true` and routes them to whichever path passed the test; a multi-speaker take that fails `verify_take` for one of its lines is split into shot / reverse-shot before a re-roll, not re-rolled blind. |
| D7 | **Voice path = (a): LTX-2.5 I2V, picture and voice made together from the prompt** (Rida, 2026-10-08, after watching batches a/b/c, stage 0 ≈ $2.30). Preferred takes: a s33 (single speaker), a s22 (2- and 3-speaker). **Multi-speaker clips are wanted and preferred** (D6 measured on path a: the turns read, identities hold). (b) Chatterbox TTS → LTX-2.5 A2V rejected: lips out of sync, and Chatterbox talks on past the line to its 1000-token cap (≈ 40 s) unless cut (the line guard). (c) LTX-2.3 ID-LoRA rejected: worse look, invents a person (a human beside a fruit), cuts an exchange to one close-up, 2–3× the GPU time. **No fallback path chosen**: the known weakness of (a) — each clip invents its voice — gets one cheap test, Chatterbox voice conversion (`FL_ChatterboxVC`, already in the image) of each line of an (a) clip to the character's locked voice, timing kept. | Step 4: 1–3 speakers per shot, multi-speaker preferred. Step 5: the clip is `ltx25_i2v_speech` with the lines quoted and each speaker's voice described in the prompt. Stage-0 rules kept: every workflow samples at cfg 1.0, so the negative prompt is ignored and a positive never names what is unwanted ("no subtitles" primed captions in 6/16 clips); I2V keeps the start image's pose, so a keyframe is drawn in the shot's own framing; the camera "holds still on the speaker" (a "push in" made (c) close in, relight and invent). The A2V and ID-LoRA workflows stay in `showrunner/workflows/` as tested alternatives, out of the main path. A clean voice reference per character stays in the cast pack only if the VC test passes. |
| D8 | **The engine is universe-agnostic; package `showrunner/`** (Rida, 2026-10-08: "fruit dramas but also any type of universe; it has to be discussed first to define the art"). Fruit is one universe among others. | Step 2 (Universe) is the gate: the medium, head types, proportions, palette, lighting and negative are agreed in chat before any picture, and `01-universe.md` opens every prompt. Nothing in the code may hard-wire fruit outside a story's own files. Stage 0 adds **one non-fruit character** (style agreed first) so D7 is not chosen on fruit faces only. Image `ghcr.io/rzdhop/showrunner-worker`. |

## Sources (research of 2026-10-08)
- LTX-2.5: https://huggingface.co/Lightricks/LTX-2.5 · https://docs.comfy.org/tutorials/video/ltx/ltx-2-5 · https://docs.ltx.io/open-source-model/usage-guides/audio-to-video · https://ltx.io/blog/what-is-id-lora · https://github.com/Lightricks/ComfyUI-LTXVideo · https://huggingface.co/Lightricks/LTX-2.5/discussions/44 (A2V lip-sync recipe)
- Wan status: https://huggingface.co/api/models?author=Wan-AI · https://www.digitalapplied.com/blog/wan-3-0-reality-check-rumor-vs-shipped
- MiniMax H3 + license: https://huggingface.co/MiniMaxAI/MiniMax-H3/blob/main/LICENSE
- Audio-driven fallbacks: https://github.com/MeiGen-AI/InfiniteTalk · https://huggingface.co/Skywork/SkyReels-V3-A2V-19B · https://comfyui-wiki.com/en/news/2026-05-21-longcat-video-avatar-1-5
- TTS: https://github.com/diodiogod/TTS-Audio-Suite
- worker-comfyui: https://github.com/runpod-workers/worker-comfyui (handler.py, Dockerfile, docs/customization.md, docs/configuration.md) · https://docs.runpod.io/serverless/workers/handler-functions · https://www.runpod.io/blog/ltx-2-5-the-open-weights-world-model-built-for-speed-and-how-to-run-it-on-runpod · https://zenn.dev/toki_mwc/articles/ltx23-vs-wan22-i2v-benchmark-rtx5090?locale=en
- Craft: https://openmicrodrama.com/guides/how-to-make-an-ai-micro-drama · https://en.wikipedia.org/wiki/Fruit_Love_Island · https://cloud.google.com/blog/products/ai-machine-learning/ultimate-prompting-guide-for-veo-3-1 · https://www.pixelsham.com/2026/04/18/creating-a-character-sheet-for-ai-videos-using-nano-banana/ · https://huggingface.co/fal/Qwen-Image-Edit-2511-Multiple-Angles-LoRA · https://github.com/zenstory-ai/drama-skills · https://github.com/jijiutong/ai-visual-director · https://github.com/ken-fs/awesome-ai-micro-drama
