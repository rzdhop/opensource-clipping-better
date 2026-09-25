# rzdhop AI — "AI Story" mode: master specification

Version 1.1 — 2026-09-25. Written for Claude Code. This is the source of truth for the
new mode; the phase prompts in this folder (`01-…` to `08-…`) reference its section
numbers instead of repeating it. Read this file in full before any phase. The author's
two inspiration videos were analysed on 2026-09-25 (`10-REFERENCE-ANALYSIS-2026-09-25.md`
in this folder, with contact sheets); the pacing rules in 6.2 and the assumptions in 14
already reflect those measurements.

---

## 0. How to work on this (read first)

**Where this file lives.** This folder is `.claude/plans/ai-story/` in the repository
and is committed to git (`.claude/` is tracked; only Docker ignores it). Two clones are
in use (a Windows workstation and the Ubuntu VPS): pull before starting a phase so the
spec, its appendix, the reference analysis and the contact sheets are on disk.

The repository already has a cross-session working-memory protocol in `.claude/`.
It applies to every phase of this work, without exception:

- **`.claude/VISION.md`** — purpose, context, "Where it stands", ordered "Next" list.
  Phase 0 rewrites it for the two-mode product (section 1).
- **`.claude/DECISIONS.md`** — append-only, IDs never renumbered. Format:
  `## DEC-NNN — <title stated as a rule>` then **Context.** / **Decision.** /
  **Consequence.** The last ID used is **DEC-091**; this work starts at **DEC-092**.
  Supersede inline ("**SUPERSEDED by DEC-0xx**"), never rewrite. If a later commit has
  already used DEC-092 (or any id), continue from the next free id — ids are append-only,
  the numbers in this spec are starting points, not reservations.
- **`.claude/ASSUMPTIONS.md`** — `- **A-NNN** — <claim>. UNCONFIRMED.` /
  `*Confirmed by …*` / `RESOLVED <date>`. Last ID is **A-029**; start at **A-030**.
  Section 14 of this spec lists assumptions to register on day one.
- **`.claude/CHECKPOINT.md`** — current task on top (Phase, Plan path, checkpoint
  commit on a clean tree, Tier-1/Tier-2 results, Audit, Open questions, Security notes,
  "Follow-ups, deliberately not done", Regression-contract table
  `| ID | Must keep working | Proven by |`, Stage ledger `| S | Stage | State |`,
  Deploy instructions). Older tasks move below the "History" divider.
- **Stages**: plan approved in chat → checkpoint commit → numbered stages, one commit
  each, riskiest stage labelled, last stage is always "docs + decisions". Merge two
  stages only when their commits could not be reverted independently.
- **Tier-1**: `python -m pytest -p no:warnings` (never pass `-q`, pyproject sets it),
  `python -m compileall`, `npm run build` in `web/dashboard`. Also the CI-equivalent env:
  `pip install --target /tmp/cilibs pytest` then
  `PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs python3 -m pytest -p no:warnings`. The suite stays
  **stdlib + pytest only** (DEC-012): every new test must import nothing else; guards
  read sources as text or with `ast`; `importorskip` only for tests that exercise the
  object. **Every behaviour test is verified to FAIL against the pre-change code.**
- **Tier-2**: no browser E2E exists; the substitute is a live run (real job, real
  browser, live endpoints) and **the human must explicitly acknowledge the substitute**
  before a task is closed.
- **Render layer**: any change to `clipping/studio/` must prove frame parity on a real
  render (framemd5 identical, or byte-identical `.ass` with md5 recorded). The new
  AI-Story renderer is a *new* module and does not touch the clip renderers; if a helper
  is shared, the parity proof is owed.
- **Five-place defaults** (config constant, `JobCreateRequest`/story request models,
  `config_adapter`, CLI, React form) get an agreement test. New options are **opt-in**.
- **No silent fallback, no silent shrinking of a request.** Failures print and are
  reported in the job activity. Links are never removed from a chain at runtime.
- **Deploy**: `docker compose rm -sfv backend && docker compose up -d --build backend`
  (sudo). Never `down -v` (Caddy certs). `.claude/` stays out of the image.
- Layouts are checked at **375 px, 820 px, 1280 px**; the app is used from a phone.
- Existing branding guard `tests/test_branding.py` (DEC-086) must be updated, not
  deleted, when the product is renamed (section 1.3).

Everything you write — code, comments, prompts, docs, DEC/A entries — is in **English**.
User-facing story output is in the story's language (FR or EN, section 6.1).

---

## 1. Product: rzdhop AI, two modes

### 1.1 Objective

Turn the fork into **rzdhop AI**, an open-source content factory with two top-level
modes sharing one backend, one job worker, one dashboard, one auth, one settings store,
one provider chain system and one deploy:

| Mode | What it does | Exists today |
|---|---|---|
| **Clips** | Long-form video → vertical highlight clips with karaoke subtitles, B-roll, BGM, hooks, metadata. | Yes (the whole current app, incl. legacy `--story-mode` multi-source assembly, which is renamed **"Story Clip (assembly)"** in the UI and left unchanged). |
| **AI Story** | A gated, step-by-step studio that turns a concept into a **persistent story workspace** (world, style lock, characters, places, props, season arc) and produces **~60-second serialized episodes** (8–12 scenes) with consistent AI characters, per-character dialogue voices, BGM/SFX, burned subtitles and a platform metadata pack — on free hosted APIs by default, on a local GPU when one is detected, and on paid APIs only by explicit opt-in with a **default ceiling of $1 per episode** (8.5). | No — this specification. |

The **value proposition** of AI Story is not "one prompt → video". It is
*consistency + serialization + retention structure*: the same characters, places, props,
palette and voices across every scene and every episode; a planned season arc with
cliffhangers and callbacks; and an episode template engineered for completion rate.

### 1.2 Non-goals (v1)

- No real-person likeness: uploaded character images are treated as *design references*
  for stylised characters; the UI states that uploading photos of real people to imitate
  them is not supported, and no face-identity adapter (InstantID/PuLID/InfiniteYou) is
  integrated (they need human faces anyway — the cast is often fruit).
- No auto-upload in v1 (the existing uploaders and the upload-guardrail decision are a
  later phase; the metadata pack is produced, the upload is not).
- No 16:9 / 1:1 render in v1 (9:16 only; scene data stays ratio-agnostic).
- No multi-user tenancy; the token model stays as is.
- No MoviePy. Rendering is an FFmpeg filtergraph built in Python.
- No copying of CC-BY-NC-SA code (huobao-drama, BigBanana-AI-Director). Patterns yes, code no.

### 1.3 Rename

- Product name **rzdhop AI** everywhere the user sees it: dashboard title, login page,
  README title/badges, `pyproject.toml` `name = "rzdhop-ai"` (the Python package
  `clipping/` keeps its import name; renaming imports is a separate decision, record it
  as a DEC "package import name stays `clipping`").
- The repository slug `opensource-clipping-better` is untouched (it is the remote).
  Update `tests/test_branding.py` to allow the slug and forbid `opensource-clipping`
  as before, and to require the string `rzdhop AI` in `web/dashboard/index.html`
  and `README.md`.
- New logo asset: `assets/images/rzdhop-ai-logo.svg` (derive from the existing
  editable SVG; keep the old one for the Clips mode header).
- Docs: `README.md` gets a "Two modes" section at the top; `docs/AI_STORY.md` is the
  user guide of the new mode (created in phase 0, extended in every phase).

### 1.4 Dashboard shell

- A **mode switch** in the top bar (`Clips` | `AI Story`) persisted in `localStorage`;
  routes `/clips/*` (all existing pages, unchanged) and `/story/*` (new pages,
  section 10). `/` redirects to the last mode.
- Both modes share Login, Settings (new sections added, section 8.6) and the health
  banner. Settings gets tabs: *Providers (LLM/STT)* (existing), *Generation (Image /
  Video / TTS / Vision)*, *Local hardware*, *Budget*.

---

## 2. Vocabulary and data model

All AI-Story data lives on disk under `outputs/stories/<story_id>/` as JSON + media,
mirrored in a small index `outputs/stories.json` (same RLock/atomic-write pattern as
`outputs/jobs.json`). Everything is plain files so a workspace can be zipped, shared
and re-imported later (an export bundle is an *optional* later feature, not a v1
requirement; the layout is simply kept self-contained). **IDs**: `story_id` and
`import_id` are 12-hex like job IDs; entity ids are `<type>_<slug>` (`char_kiwilo`,
`place_beach_camp`, `prop_coconut_phone`); scene, shot and line ids are `sNN`, `shNN`,
`lNN`. Every JSON document carries `"$schema": "<name>_v1"` (the repo already uses this
convention) and an `updated_at` ISO timestamp (omitted from the examples below for
brevity, mandatory in the schemas).

```
outputs/stories/<story_id>/
  story.json                # StoryBible (2.1)
  style_lock.json           # StyleLock (2.2), frozen copy of the template + user edits
  styles/custom_<iid>.json  # custom style templates created from imports (12), optional
  season.json               # SeasonArc (2.6)
  cost_ledger.json          # story-level CostLedger (2.11): cast/places/season spending + every episode
  characters/<char_id>/
      character.json        # Character (2.3)
      refs/                 # approved reference images: portrait.png, turnaround.png,
                            # expressions.png, extra_01.png…  (+ uploads/ for user images)
      voice_sample.mp3      # 3-second TTS sample of the assigned voice
  places/<place_id>/
      place.json            # Place (2.4)
      refs/master_plate.png # + variants: night.png, rain.png…
  props/<prop_id>/
      prop.json, refs/
  episodes/ep<NN>/
      script.json           # EpisodeScript (2.7): scenes → lines
      storyboard.json       # Storyboard (2.8): shots with resolved prompts
      assets/
        shots/shot_<NN>.png          # keyframe per shot (Tier 1 = the frame shown)
        shots/shot_<NN>.mp4          # Tier 2/3 animated clip (optional)
        voice/line_<NN>.mp3 + .json  # audio + word timestamps
        sfx/, bgm/                   # resolved cue files (copied, so the bundle is complete)
      subtitles.ass
      render_manifest.json  # RenderManifest (2.9)
      episode_final.mp4     # 1080x1920, 30 fps CFR, AAC
      metadata_pack.json    # MetadataPack (2.10) + cover.png
      cost_ledger.json      # per-episode view of the ledger (same entries, filtered by ep)
  imports/<import_id>/      # reference-video analysis (section 12)
  activity.log              # the same stdout-tee lines the job worker prints
```

### 2.1 StoryBible — `story.json`

```json
{
  "$schema": "story_bible_v1",
  "story_id": "a1b2c3d4e5f6",
  "title": "Tentafruit Island",
  "language": "fr",                       // "fr" | "en"  (6.1)
  "concept_id": "tentafruit_island",      // from the library (7) or "custom"/"import:<id>"
  "logline": "…one sentence…",
  "premise": "…3–5 sentences…",
  "genre_tags": ["soap", "survival", "comedy"],
  "tone": "melodramatic, self-aware, fast",
  "world": {
    "setting_summary": "…",
    "rules": ["Fruits are people. Nobody comments on it.", "…"],
    "time_period": "contemporary",
    "recurring_motifs": ["the coconut phone", "the elimination bonfire"]
  },
  "themes_and_values": ["loyalty vs ambition", "…"],   // the 'real value' the concept carries (7)
  "audience": {"age": "13+", "platforms": ["tiktok", "shorts", "reels"]},
  "cast_ids": ["char_…"], "place_ids": ["place_…"], "prop_ids": ["prop_…"],
  "style_template_id": "fruit_drama",
  "episode_template_id": "serial_60s_v1",           // (6.2)
  "generation_profile": {"tier": 1, "route": "auto", "consistency_mode": "references", "budget_profile": "free"},
                                 // tier 1|2|3 is the story default (a shot may be kept still);
                                 // route auto|local|api (8); consistency_mode references|prompt_only (8.1);
                                 // budget_profile free|one_dollar|quality (8.5) — one_dollar implies tier 2 on key shots
  "status": "bible_approved",   // draft → concept_chosen → bible_approved → style_approved
                                 // → cast_approved → places_approved → ready
  "created_at": "...", "updated_at": "..."
}
```

### 2.2 StyleLock — `style_lock.json`

A frozen, fully-expanded copy of the chosen style template (section 5) plus the
story-specific overrides. It is **injected verbatim** into every image, video and
voice-direction prompt, so a later template edit never changes an existing story.

```json
{
  "$schema": "style_lock_v1",
  "template_id": "fruit_drama", "template_version": 1,
  "rendering": "…", "palette": {"primary": ["#…"], "accents": ["#…"], "forbidden": ["neon green"]},
  "camera": "…", "lighting": "…", "character_design_rules": "…", "environment_rules": "…",
  "negative_prompt": "…",
  "motion_rules": {
    "tier1": {
      "default_motion": "push_in",                       // from the camera_motion list (6.3)
      "by_function": {"hook": "push_in", "peak": "push_in", "cliffhanger": "push_in", "wide_establishing": "pan_lr"},
      "zoom": {"dialogue": [1.00, 1.10], "peak": [1.00, 1.18], "max": 1.20},
      "pan_pct": 4,
      "modifiers": ["handheld"],                          // optional per-shot modifiers from 6.3
      "overlays": []                                      // e.g. ["paper_texture"] (6.3)
    },
    "tier2_prompt_suffix": "…"
  },
  "typography": {"font_family": "…", "subtitle_mode": "word_pop", "subtitle_style": "…", "overlay_style": "…", "ai_label": true},
  "episode_defaults": {"hook_style": "insert_prop", "cliffhanger_style": "hard_stop", "shots_per_scene": [2, 4], "max_places": 2, "episode_template_id": "serial_60s_v1"},
  "audio": {
    "bgm_moods": ["telenovela_tension", "…"],
    "emotion_to_mood": {"tension": "telenovela_tension", "tender": "tropical_drama", "shocked": "suspense_sting", "default": "telenovela_tension"},
    "sfx_pack": "soap",
    "sfx_cues": ["gasp_crowd", "dramatic_sting", "slap", "phone_ring", "waves_soft", "door_slam", "heartbeat"],
    "voice_direction": "…"
  },
  "overrides": {"palette.accents": ["#FFD400"]},
  "locked_at": "..."
}
```

`motion_rules.tier1`, `audio.sfx_cues` and `audio.emotion_to_mood` are part of the
style-template schema (5) and are validated against the closed lists of 6.3 and the sfx
packs of 11. E2 enumerates `audio.sfx_cues` when it writes cues; the BGM picker uses
`emotion_to_mood` with the episode's dominant scene emotion.

### 2.3 Character — `characters/<id>/character.json`

```json
{
  "$schema": "character_v1",
  "char_id": "char_kiwilo", "name": "Kiwilo", "role": "lead",   // lead | support | recurring | guest
  "archetype": "charming schemer",
  "descriptor": "an anthropomorphic kiwi with fuzzy brown skin, bright green flesh visible at the mouth, a thin gold chain, a white linen shirt open at the collar",
  "signature_items": ["thin gold chain", "white linen shirt", "left-eyebrow scar"],
  "personality": {"traits": ["…"], "wants": "…", "fears": "…", "speech_style": "short sentences, always deflects with a joke"},
  "relationships": {"char_mangella": "secret ex", "char_broccolia": "owes her money"},
  "voice": {"provider": "edge", "voice_id": "fr-FR-HenriNeural", "rate": "+5%", "pitch": "-2Hz", "direction": "smug, warm, slightly nasal"},
  "refs": {"portrait": "refs/portrait.png", "turnaround": "refs/turnaround.png", "expressions": "refs/expressions.png", "extra": [], "uploads": []},
  "ref_seed": 123456789,          // seed used for the portrait; reused when the provider honours seeds
  "prompt_block": "…auto-assembled from descriptor + signature_items + style_lock.character_design_rules…",
  "state": {"alive": true, "location": "place_beach_camp", "arc_notes": ["ep01: revealed to have lied about the coconut"]},
  "approved": true
}
```

Rules: prompts always reference a character by **appearance (descriptor + signature
items), never by name** — names mean nothing to image models. The `prompt_block` is
regenerated deterministically from fields (pure function, unit-tested).

### 2.4 Place — `places/<id>/place.json`

`place_id, name, descriptor, layout_notes` (what is left/right/back, for continuity),
`time_variants` (day/night/rain… each with a ref image or `null`), `master_plate`
path, `prompt_block`, `approved`.

### 2.5 Prop — `props/<id>/prop.json`

`prop_id, name, descriptor, owner_char_id|null, ref, prompt_block, approved`.

### 2.6 SeasonArc — `season.json`

```json
{
  "$schema": "season_arc_v1",
  "episodes_planned": 8,
  "arc": [
    {"ep": 1, "function": "setup", "summary": "…", "open_hooks_out": ["who stole the coconut phone"]},
    {"ep": 2, "function": "escalation", "summary": "…", "open_hooks_in": ["…"], "open_hooks_out": ["…"]},
    {"ep": 5, "function": "midpoint_twist", "summary": "…"},
    {"ep": 8, "function": "climax_and_reset", "summary": "…"}
  ],
  "series_memory": {                      // updated after every approved episode
    "recaps": {"ep01": "…≤40 words…"},
    "open_hooks": ["…"],
    "relationship_state": {"char_kiwilo|char_mangella": "publicly enemies, secretly allies"},
    "introduced": {"ep03": ["char_new_…"]}
  },
  "audience_feedback": [{"ep": 1, "pasted_at": "...", "text": "…top comments / stats…", "digest": "…LLM digest ≤60 words…"}]
}
```

### 2.7 EpisodeScript — `episodes/epNN/script.json`

```json
{
  "$schema": "episode_script_v1",
  "ep": 1, "title": "…", "language": "fr",
  "target_duration_s": 60, "duration_window_s": [55, 75],
  "hook": {"on_screen_text": "…≤ 6 words…"},           // the hook's spoken line is s01's first line (written by E3, see 4.2)
  "scenes": [
    {                                                  // ep ≥ 2 only: a recap scene comes first
      "scene_id": "s00", "function": "recap",          // ≤ 3 s, one line or on-screen text paying off the previous cliffhanger
      "place_id": "place_beach_camp", "time_variant": "day", "characters": ["char_kiwilo"], "props": [],
      "summary": "…", "emotion": "tension", "target_duration_s": 3,
      "lines": [{"line_id": "l00", "speaker": "char_kiwilo", "text": "…", "emotion": "tension", "delivery": "urgent"}],
      "sfx_cues": [], "on_screen_text": "PRÉCÉDEMMENT"
    },
    {
      "scene_id": "s01", "function": "hook",           // recap | hook | setup | rising | peak | turn | cliffhanger
      "place_id": "place_beach_camp", "time_variant": "day",
      "characters": ["char_kiwilo", "char_mangella"],
      "props": ["prop_coconut_phone"],
      "summary": "…one sentence of what happens…",
      "emotion": "tension", "target_duration_s": 6,   // scene emotion: closed list (6.3), drives BGM/motion
      "lines": [
        {"line_id": "l01", "speaker": "char_mangella", "text": "…", "emotion": "angry", "delivery": "fast, whispered"},
        {"line_id": "l02", "speaker": "char_kiwilo",  "text": "…", "emotion": "fear", "delivery": "defensive"}
      ],                                               // line emotion: closed list (6.3); delivery: free text for the voice
      "sfx_cues": [{"at": "start", "cue": "waves_soft"}, {"at": "l02", "cue": "gasp_crowd"}],
      "on_screen_text": null
    }
  ],
  "cliffhanger": {"scene_id": "s10", "reveal": "…", "cut_to_black": true},
                                                       // end card text is deterministic Python, not model output:
                                                       // "PART {n+1}" (en) / "PARTIE {n+1}" (fr) + story title
  "next_episode_teaser": "…one sentence, ≤ 15 words…", // goes into the metadata pack (description / pinned comment)
  "consistency_report": {"passed": true, "notes": []},     // 4.3
  "approved": false
}
```

`speaker` is a cast id, or the literal `"narrator"` when the story has opted into a
narrator (`story.json.narrator: {"enabled": false, "voice": {...}}`, default off — the
concept library's Fruit Drama concepts never use one; storybook concepts may).

### 2.8 Storyboard — `episodes/epNN/storyboard.json`

**2–4 shots per scene** by default (`episode_defaults.shots_per_scene`; the reference
videos average 3 shots per beat): typically an establishing/two-shot, one shot per
speaking character, a reaction close-up or a prop insert. Each shot is its own keyframe
(Tier 1) or clip (Tier ≥ 2); a shot may last as little as 0.8 s (reaction cut). Shots of
the same scene share the place plate and lighting, and differ by framing.

```json
{
  "$schema": "storyboard_v1",
  "shots": [
    {
      "shot_id": "sh01", "scene_id": "s01", "order": 1,
      "framing": "medium_two_shot", "camera_motion": "push_in",          // closed lists (6.3)
      "modifiers": [],                                                    // e.g. ["handheld"] (6.3)
      "subject_tags": ["@char_kiwilo", "@char_mangella", "#place_beach_camp:day", "%prop_coconut_phone"],
      "action": "Mangella holds up the cracked coconut phone; Kiwilo raises both hands",
      "image_prompt": "…fully resolved: style_lock + character prompt_blocks + place prompt_block + action + framing…",
      "negative_prompt": "…",
      "reference_images": ["characters/char_kiwilo/refs/portrait.png", "…", "places/place_beach_camp/refs/master_plate.png"],
      "duration_s": 6.0,                       // = sum(line audio) + pauses, computed in Python (6.4)
      "keep_still": false,                     // per-shot override of the story tier: true = Tier-1 motion even in a tier-2 story
      "motion": {"type": "push_in", "zoom_from": 1.0, "zoom_to": 1.12, "pan": "none"},
      "video_prompt": null,                    // tier ≥ 2: motion description for the I2V model
      "assets": {"image": "assets/shots/shot_01.png", "video": null, "seed": 998877, "provider": "gemini/…", "approved": false}
    }
  ],
  "transitions": [{"after": "sh01", "type": "cut"}, {"after": "sh03", "type": "dissolve", "duration_s": 0.4}],
  "approved": false
}
```

### 2.9 RenderManifest — `render_manifest.json`

Inputs (every asset path with sha256), the exact FFmpeg command lines (one per stage),
output path, duration, loudness measurement, `framemd5` of the output, timings. This is
what a Tier-2 parity check compares.

### 2.10 MetadataPack — `metadata_pack.json`

Per platform (`tiktok`, `shorts`, `reels`): `title`, `description` (ends with the
`next_episode_teaser`), `hashtags[]`, `hook_text`, `pinned_comment` (the teaser + "PART
n+1" call), `cover` path, plus `title_en`/`hashtags_en` when the story language is FR.
Generated by one small LLM call per platform (section 4.2, prompt M1).

### 2.11 CostLedger — `cost_ledger.json` (story level)

Append-only list `{ts, ep|null, step, provider, model, unit ("image"|"second"|"char"|"token"),
qty, est_usd, paid: bool}`. `est_usd` uses the price table in
`clipping/providers/pricing.py` (section 8.5). The story page shows the running total;
the budget caps (8.5) read it; `episodes/epNN/cost_ledger.json` is a filtered view
written for the bundle. `data/usage.json` is a different thing: the free-tier daily
counters (8.4).

---

## 3. The creation workflow (gated at every step)

Every step is a **story job** (section 9) that produces or updates one document above,
ends in `awaiting_approval`, and cannot spend on the next step until the user approves
(or edits and approves). Every generated item is individually **regenerable** (with an
optional free-text note: "make him older", "less neon") and **lockable**.

| # | Step | Input | LLM/gen calls | Output | Approval unit |
|---|---|---|---|---|---|
| 1 | **New story** | language, optional free text, optional uploaded reference video (12), optional style pick | — | draft `story.json` | — |
| 2 | **Concepts** | step 1 | 0 (library) or 1 call per 5 concepts (prompt C1) | 10 concept cards (library 7 + "Generate 10 more") | pick one → `concept_chosen` |
| 3 | **Bible** | concept | B1 (logline+premise+tone), B2 (world rules+motifs), B3 (themes/values+audience) — 3 small calls | `story.json` filled | approve → `bible_approved` |
| 4 | **Style** | bible | 0 | `style_lock.json` from template + palette/typography tweaks; live preview strip: 3 tiny sample images (optional, costs images); consistency mode confirmed (`references` or explicit `prompt_only`, 8.1) | approve → `style_approved` |
| 5 | **Cast** | bible | K1 per character (descriptor, signature items, personality, voice direction) — one call each; then image gen: portrait → turnaround → expressions (3 images/character); TTS sample | `characters/*` | approve each character (regenerate text / image / voice separately) |
| 6 | **Places & props** | bible + cast | P1 per place, R1 per prop; 1 master plate image per place (+ variants on demand), 1 image per prop | `places/*`, `props/*` | approve each → `places_approved` |
| 7 | **Season arc** | all above | S1 (arc skeleton, N episodes), then S2 per episode summary (one call each) | `season.json` | approve → `ready` |
| 8 | **Episode script** | arc entry + series memory | E1 (beat sheet: 8–12 scene stubs), then E2 **per body scene** (lines, sfx, emotion; receives the previous scene's summary and last line), E3 (hook scene lines + on-screen text, cliffhanger scene reveal + line, recap scene for ep ≥ 2, teaser), E4 consistency check | `script.json` | approve (edit any line inline) |
| 9 | **Storyboard** | script | T1 per scene (framing, camera, action, prompt fragments; receives the previous two shots' framing) — or fully deterministic from templates when the user picks "fast"; then a deterministic pass enforces the cross-scene rules of 6.2 (no two consecutive identical framings, reaction close-up every 3 scenes) | `storyboard.json` with resolved prompts | approve (edit prompts) |
| 10 | **Assets** | storyboard | image per shot (with refs), TTS per line, SFX/BGM resolution, optional I2V per shot (tier ≥ 2) | `assets/*` | approve grid (regenerate any shot/line) |
| 11 | **Render** | assets | 0 LLM | `episode_final.mp4`, `subtitles.ass`, manifest | preview, then "Publish pack" |
| 12 | **Metadata pack** | script + render | M1 per platform | `metadata_pack.json`, cover | done |
| 13 | **Next episode** | approved episode | step `memory`: S3 (recap, hooks, relationship state); step `feedback`: F1 (audience feedback digest); step `propose-next`: N1 (new characters/twists) | `season.json` updated → back to step 8 (and 5/6 for new characters/places) | approve |

A **"Fast track"** button chains steps 8 → 12 with auto-approval for users who trust the
defaults; it still stops before any *paid* spending unless `allow_paid` is on and the
episode estimate fits `per_episode_cap_usd` (8.5); under the `one_dollar` profile it
shows the planned split ("20 images $0.60 + 3 animated shots $0.33 = $0.93") once, then
runs.

Job semantics of a step (9.1): the step job ends in `awaiting_approval`, which is
**terminal for the worker** (the single concurrency slot is freed); approving flips the
job to `done`, rejecting/regenerating creates a new job. The job list groups story-step
jobs under their story.

---

## 4. LLM usage rules and the prompt catalogue

### 4.1 Rules (extend DEC-027 to story writing — record as a new DEC)

- **One request → one artifact ≤ ~400 output tokens.** A bible is 3 calls, a script is
  1 + N + 1 + 1 calls. Never ask for a whole episode in one response. This keeps the
  free/slow providers usable and makes every piece regenerable on its own.
- The model outputs **JSON against a schema** through the existing `complete_json`
  negotiation ladder (`json_schema → json_object → prompt-only` + salvage). Schemas live
  in `clipping/aistory/schemas.py` as plain dicts (stdlib), one per prompt.
- **The model never outputs durations, timestamps or file paths.** Python computes
  durations from TTS audio; the model gives `target_duration_s` hints only, which are
  clamped by the episode template.
- Temperatures: reuse `ANALYTIC_TEMPERATURE` (0.2) for checks/digests and
  `WRITING_TEMPERATURE` (0.5) for creative steps; add `IDEATION_TEMPERATURE = 0.9` for
  concepts and twists.
- All prompts are in **English**, with an explicit `Write all user-facing text in
  {language_name}.` instruction; they live in `clipping/aistory/prompts.py` with a
  `PROMPT_VERSION` constant (same pattern as `clipping/analysis/prompts.py`) and each
  prompt has a pure builder function `build_<id>(ctx) -> (system, user, schema)` that is
  unit-tested with fixtures (deterministic strings, no network).
- Every prompt receives a **context pack** built by `context.py`: bible summary
  (≤ 120 words), style tone line, the involved characters' `personality` + `speech_style`
  (never their visual descriptor — that is for images), the place's `layout_notes`, the
  relevant series memory, and for per-scene/per-shot prompts the **local continuity**:
  E2 gets the previous scene's summary and its last line, T1 gets the previous two shots'
  framing and camera motion. Token-budget the pack (≤ ~1,200 input tokens) so the free
  tiers' TPM survive.
- Caps below are *output* caps chosen so JSON never truncates on the slow providers; a
  truncated response is a failure (salvage is attempted, then the call is retried once
  with the same cap, then reported — never silently shrunk).
- The LLM chain is the existing `LLM_CHAIN`. Story writing uses the same chain;
  ideation may set `prefer_models=[…]` later, but not in v1.

### 4.2 Prompt catalogue (ids used in section 3)

| ID | Purpose | Key output fields | Cap |
|---|---|---|---|
| C1 | **2 concepts** per call (5 calls for "10 more"), seeded by language, style, optional user text, optional import digest | `concepts[2]{title, logline, world, cast_sketch[3-5]{name, role, one_line}, hook_formula, value, retention_mechanics, style_fit}` | 500 |
| B1 | logline, premise, tone, genre tags | | 250 |
| B2 | world: setting, 4–6 rules, time period, 3 motifs | | 250 |
| B3 | themes/values, audience, 3 "why people come back" lines | | 200 |
| K1 | one character from a cast sketch: descriptor (≤ 45 words, appearance only), 2–3 signature items, personality, wants/fears, speech_style, voice direction, relationships to existing cast | | 350 |
| P1 / R1 | one place / one prop: descriptor, layout notes, time variants / owner | | 250 |
| S1 | season arc skeleton for N episodes: function + one-line summary each | | 400 |
| S2 | expand one arc entry: summary (≤ 60 words), hooks in/out, characters involved | | 250 |
| E1 | beat sheet: 8–12 scene stubs {function, place, characters, summary, emotion, target_duration_s} following the episode template (6.2); summaries ≤ 15 words | | 600 |
| E2 | one **body** scene (setup/rising/peak/turn): 1–4 lines {speaker, text ≤ 22 words, emotion, delivery}, sfx cues from `audio.sfx_cues`, optional on-screen text | | 300 |
| E3 | the framing scenes: hook scene lines (1–2) + on-screen text ≤ 6 words, cliffhanger scene reveal + ≤ 1 line, recap scene line (ep ≥ 2), next-episode teaser. E2 never writes these scenes. | | 350 |
| E4 | consistency check over the whole script: continuity errors vs bible/series memory, characters speaking out of character, place mismatch → `{passed, issues[]{scene_id, kind, fix}}` (analytic temp) | | 350 |
| T1 | one scene → 1–2 shots: framing, camera motion (closed list), action sentence, subject tags | | 250 |
| M1 | metadata for one platform | | 300 |
| S3 | after approval: recap ≤ 40 words, open hooks, relationship state deltas | | 250 |
| F1 | digest pasted audience feedback (≤ 60 words + 3 suggested directions) | | 250 |
| N1 | propose ≤ 2 new characters and ≤ 2 twists for the next episode, consistent with arc | | 350 |
| V1 | vision: describe sampled frames of a reference video (rendering style, palette hexes, lighting, camera habits, character design language, on-screen text presence and overlay typography) → style fragments | | 450 |
| V2 | from transcript + frame descriptions: premise skeleton, conflict, character archetypes (no names) | | 400 |

### 4.3 Consistency check (E4) is mandatory

The script cannot be approved while `consistency_report.passed` is false unless the user
ticks "approve anyway" (recorded in the script). Issues are shown inline on the scene.

---

## 5. Style templates (fully pre-prompted)

Templates are data, not code: `clipping/aistory/templates/styles/<id>.json`, validated
by a test against a schema, versioned (`"version": 1`). The **reference-sheet prompt**,
**master-plate prompt** and **shot prompt** are string templates with `{placeholders}`
filled by `prompting.py`. Below is the complete content of the seven shipped templates;
copy them into the JSON files verbatim (adjust only formatting).

Common placeholders: `{descriptor}`, `{signature_items}`, `{place_descriptor}`,
`{time_variant}`, `{action}`, `{framing}`, `{emotion}`, `{palette_line}`.

Common **shot prompt skeleton** (every template uses this order — order matters for
diffusion models: subject → action → setting → style → camera → lighting → quality):

```
{subjects_block}. {action}. Setting: {place_block}, {time_variant}.
Style: {rendering}. Palette: {palette_line}. {character_design_rules}
Camera: {framing_phrase}, {lens_phrase}. Lighting: {lighting}. Vertical 9:16 composition,
subject kept in the central safe area (leave the bottom 22% free of faces for subtitles).
{quality_tail}
```

`{framing_phrase}` and `{lens_phrase}` come from a per-shot table
(`framing → phrase`, e.g. `medium_two_shot → "medium two-shot, both characters
waist-up"`, `close_up → "tight close-up on the face"`; `lens_phrase` derived from the
template's `camera` block and the framing, e.g. `"50mm look, shallow depth of field"`).
The template-wide `camera` paragraph is **not** injected into shot prompts — it guides
T1 (it is in T1's context) and the deterministic "fast" storyboard, never the image model.

Common **negative prompt base** (each template appends its own):
`text, watermark, logo, signature, extra limbs, extra fingers, deformed hands, duplicated
character, cropped face, blurry, low resolution, jpeg artifacts, out of frame, split
screen, collage, frame border, caption`.

Common **reference-sheet prompts** (3 images per character):

1. *Portrait*: `Character portrait, {descriptor}, wearing {signature_items}. Neutral
   expression, looking at camera, three-quarter view, chest-up. {rendering}.
   {character_design_rules} Plain {sheet_background} background, even soft studio
   lighting, no props, no text. Vertical 9:16.`
2. *Turnaround*: `Character design turnaround sheet of the same character: {descriptor},
   {signature_items}. Four full-body views side by side in one row: front, three-quarter,
   profile, back. Identical proportions and outfit in every view. {rendering}.
   {character_design_rules} Plain {sheet_background} background, flat even lighting,
   no text, no labels.` (uses the portrait as reference image when the provider supports
   references)
3. *Expressions*: `Expression sheet of the same character: {descriptor}, {signature_items}.
   Six head-and-shoulders portraits in a 3x2 grid: neutral, happy, angry, shocked, sad,
   scheming. Same face, same outfit, same lighting in every cell. {rendering}. Plain
   {sheet_background} background, no text.`

Common **master-plate prompt**: `Establishing wide shot of {place_descriptor},
{time_variant}, no people, no characters. {environment_rules} {rendering}. Palette:
{palette_line}. Camera: wide, eye level, 24mm equivalent. Lighting: {lighting}. Vertical
9:16, horizon in the upper third, foreground detail in the lower third. {quality_tail}`

#### 5.1 `fruit_drama` — Fruit Drama (anthropomorphic food, photoreal 3D)

- **rendering**: "photorealistic 3D render of anthropomorphic fruits and vegetables with
  expressive human-like faces (eyes, brows, mouths) on realistic fruit heads, human-
  proportioned bodies in real fabric outfits, subsurface scattering on fruit skin,
  visible pores and fuzz, glossy highlights, high-end CGI commercial quality, Octane-
  style render"
- **palette**: primary `["#F2C14E","#E4572E","#3A7D44"]`, accents `["#FFFFFF","#1E1E24"]`,
  forbidden `["neon green","hot pink backgrounds"]`; palette_line "saturated natural
  fruit colours against warm neutral sets"
- **camera**: "telenovela coverage: medium two-shots for dialogue, tight close-ups for
  reactions, slow push-in on reveals, 50mm look, shallow depth of field"
- **lighting**: "warm key light with a soft cool fill, golden-hour or practical interior
  lamps, dramatic rim light on reveals"
- **character_design_rules**: "The head is one recognisable whole fruit or vegetable at
  human head scale; the face (eyes, brows, mouth with teeth) is carved into its surface,
  not pasted on. Bodies are human, dressed in realistic contemporary clothes that carry
  the character's signature items and tell their social status (a torn tee and backpack
  vs a black suit and tie). No hands as fruit — hands are human. Keep exact fruit
  species, ripeness, colour and outfit identical in every image."
- **environment_rules**: "real-world sets — manor gates, gravel courtyards, derelict
  interiors with chandeliers, beach camps, villa kitchens, restaurants — photographed like
  a reality-TV show or a live-action comedy, props at human scale; exteriors in golden
  hour, interiors cold blue-grey with warm candle or lamp practicals"
- **episode_defaults**: `hook_style: insert_prop` (a sign, a contract, a phone screen that
  states the premise in ≤ 5 words), `cliffhanger_style: hard_stop`, `shots_per_scene:
  [2, 4]`, `max_places: 2`; **subtitle_mode `word_pop`**, `ai_label: true`
- **negative** (appended): "cartoon, 2D, flat shading, anime, fruit with stick limbs,
  fruit bowl, food photography, human head"
- **motion_rules.tier1**: default `push_in` 1.00→1.10 over the shot for dialogue,
  `push_in` 1.00→1.18 on peak/cliffhanger shots, `pan_lr` ±4 % on establishing shots,
  30 fps, ease-in-out; **tier2_prompt_suffix**: "subtle natural head and shoulder
  movement, realistic blinking, breathing, cloth sway, camera slowly pushes in, no
  morphing, no extra characters entering"
- **typography**: font "Montserrat ExtraBold" (already in `custom_fonts` if present,
  else "Inter Black"), subtitle "white fill, black 3 px outline, yellow `#FFD400` karaoke
  highlight, 62 px at 1080 wide, bottom 18 %", overlay "uppercase, 84 px, drop shadow,
  centered upper third, ≤ 6 words"
- **audio**: bgm_moods `["telenovela_tension","tropical_drama","suspense_sting"]`,
  sfx_pack "soap" (gasp_crowd, dramatic_sting, slap, phone_ring, waves_soft, door_slam,
  heartbeat), voice_direction "over-acted telenovela delivery, exaggerated emotion,
  crisp diction, quick pace"
- **sheet_background**: "light grey"; **quality_tail**: "ultra detailed, 8k, sharp focus"

#### 5.2 `family_3d` — 3D animated family film

- **rendering**: "high-end 3D animated feature film look, stylised proportions with
  large expressive eyes, soft rounded shapes, subsurface skin, detailed fabric and fur
  textures, global illumination, cinematic depth of field" (no studio names anywhere)
- **palette**: primary `["#6B7A8F","#F28C28","#8BC34A"]`, accents `["#FFFFFF","#5D3A9B"]`,
  forbidden `["neon", "rainbow backgrounds"]`; palette_line "muted grey-blue environment
  with one warm saturated accent on the character, soft pastel shadows" (the reference
  video: grey ruined room, orange-skinned mother as the single warm element)
- **camera**: "animated-feature coverage: wide establishing, medium shots at character
  eye level, expressive close-ups, gentle dolly moves, 35mm look"
- **lighting**: "soft warm key, bounced fill, glowing rim light, volumetric sunlight
  through windows or leaves"
- **character_design_rules**: "Each character has one silhouette-defining shape, one
  primary colour and 2–3 signature items that never change. Eyes are large and readable
  at phone size. Expressions are broad and clear. The character carries the only warm,
  saturated colour in the frame; the environment stays muted."
- **environment_rules**: "cosy or humble interiors with one strong light source (a
  window, a lamp), slightly oversized props, rounded architecture, rich set dressing
  that tells the character's story; reuse the same setup reframed (single, close-up,
  two-shot, hands insert) rather than new angles"
- **episode_defaults**: `hook_style: shocking_image`, `cliffhanger_style: cut_to_black`,
  `shots_per_scene: [2, 4]`, `max_places: 1`, scene clamp for `peak`/`tender` raised to
  12 s (long emotional holds); **subtitle_mode `none`**, `ai_label: true`
- **negative**: "photorealistic human, live action, anime, 2D, creepy, uncanny,
  realistic gore"
- **motion_rules**: tier1 `push_in` 1.00→1.08 dialogue, `pan_ud` on tall sets, 30 fps;
  tier2 suffix "gentle character animation, squash-and-stretch feel, soft camera dolly,
  consistent character model"
- **typography**: "Fredoka Bold / Baloo 2", subtitles white with dark blue outline,
  highlight `#FFB703`; overlays rounded, playful
- **audio**: bgm `["whimsical_adventure","warm_family","light_mischief"]`, sfx_pack
  "cartoon_soft" (boing, whoosh_soft, twinkle, pop, footsteps_tiny), voice_direction
  "warm, clear, animated, child-friendly energy"
- sheet_background "warm cream"; quality_tail "highly detailed, rendered in 4k"

#### 5.3 `anime` — Anime / manga

- **rendering**: "modern TV anime cel shading, clean line art, two-tone shadows, vivid
  colours, detailed painted backgrounds, dramatic screentone-free digital finish"
- **palette**: primary `["#2B2D42","#EF233C","#EDF2F4"]`, accents `["#8D99AE","#FFD166"]`;
  palette_line "high contrast, deep shadows, one hot accent colour per scene"
- **camera**: "anime shot grammar: extreme close-up on eyes for tension, low-angle hero
  shots, dutch tilt on reveals, speed-line backgrounds on action, still 'pillow shots'
  of the setting"
- **lighting**: "hard rim light, dramatic backlight silhouettes, coloured ambient
  (sunset orange, night blue), lens flare on reveals"
- **character_design_rules**: "Distinct hair silhouette and colour per character, a
  fixed eye colour and shape, one accessory as signature item. Faces stay on-model:
  same eye spacing, same jawline."
- **environment_rules**: "painterly backgrounds with atmospheric perspective, wires,
  signs (without readable text), rain-slick streets, cherry blossoms or neon as motifs"
- **negative**: "3D render, photorealistic, western cartoon, chibi (unless requested),
  extra eyes, readable text on signs"
- **motion_rules**: tier1 `push_in` 1.00→1.12, occasional `pan_lr` on wide shots, allow
  `hold` (no motion) on 'pillow shots'; tier2 suffix "limited animation feel, hair and
  cloth sway, slow camera push, no lip flapping"
- **typography**: "Bangers / Noto Sans JP Black", subtitles white with red outline,
  highlight `#FFD166`; overlays angled, impact-style
- **audio**: bgm `["anime_tension","emotional_piano","battle_synth"]`, sfx_pack
  "anime" (whoosh_sharp, impact_hit, heartbeat, rain_loop, chime), voice_direction
  "intense, clipped, dramatic pauses"
- sheet_background "white"; quality_tail "masterpiece, best quality, sharp line art"

#### 5.4 `cinematic_real` — Realistic cinematic

- **rendering**: "live-action cinematic still, 35mm film grain, anamorphic lens
  characteristics, natural skin texture, realistic materials, shot on a digital cinema
  camera"
- **palette**: primary `["#0B1C2C","#D98E04","#5C6B73"]`, accents `["#F2F2F2"]`;
  palette_line "teal-and-orange grade with lifted blacks; noir variant desaturated with
  one warm practical"
- **camera**: "thriller coverage: long lens compressions, handheld micro-shake for
  unease, static wides for dread, over-the-shoulder for dialogue, 50–85mm looks"
- **lighting**: "motivated practical light, low-key, strong contrast, sodium street
  lamps or cold fluorescents, silhouettes"
- **character_design_rules**: "Characters are fictional adults with one memorable
  physical trait and 2–3 signature wardrobe items; never resembling a specific real
  person. Keep age, build, hairstyle and wardrobe identical."
- **environment_rules**: "real, lived-in locations: night buses, diners, offices,
  parking garages; weathered surfaces, reflections, rain"
- **negative**: "cartoon, anime, 3D render, illustration, oversaturated, smooth plastic
  skin, celebrity likeness"
- **motion_rules**: tier1 `push_in` 1.00→1.06 (slow), `pan_lr` ±3 %, add `handheld`
  micro-jitter option (±2 px sinusoidal) on tension scenes; tier2 suffix "photoreal,
  natural human motion, subtle handheld camera, film grain, no morphing"
- **typography**: "Bebas Neue / Oswald", subtitles white with 2 px black outline, highlight
  `#D98E04`; overlays small caps, letter-spaced
- **audio**: bgm `["dark_ambient","pulse_thriller","noir_jazz"]`, sfx_pack "real"
  (room_tone, footsteps_concrete, car_pass, phone_buzz, breath), voice_direction
  "grounded, low, restrained, realistic pacing"
- sheet_background "dark charcoal"; quality_tail "photorealistic, cinematic, 8k"

#### 5.5 `cartoon_flat` — 2D cartoon / flat

- **rendering**: "bold 2D cartoon, thick uniform outlines, flat colour fills, minimal
  shading, exaggerated expressions and poses, clean vector look"
- **palette**: primary `["#FF6B6B","#4ECDC4","#FFE66D"]`, accents `["#1A535C","#FFFFFF"]`;
  palette_line "flat saturated primaries, cream paper background"
- **camera**: "simple staging: medium shots, straight-on or slight angle, occasional
  extreme close-up for comedy beats, no depth of field"
- **lighting**: "flat, no cast shadows except a soft ground shadow"
- **character_design_rules**: "Simple geometric bodies, one silhouette and one colour
  per character, dot or oval eyes, signature items drawn as bold shapes. Line weight
  identical everywhere."
- **environment_rules**: "minimal backgrounds with 2–3 props, flat horizon, patterned
  textures allowed"
- **negative**: "3D, photorealistic, gradients, painterly, sketchy lines, anime"
- **motion_rules**: tier1 `pan_lr` and `hold` preferred, `push_in` ≤ 1.05, 30 fps; tier2
  suffix "snappy 2D animation, limited frames feel, bouncy motion"
- **typography**: "Luckiest Guy / Comic Neue Bold", subtitles dark navy on cream pill
  background, highlight `#FF6B6B`
- **audio**: bgm `["quirky_ukulele","sitcom_bounce","cartoon_chase"]`, sfx_pack
  "cartoon" (boing, honk, slide_whistle, pop, record_scratch), voice_direction
  "bright, comedic timing, exaggerated"
- sheet_background "cream"; quality_tail "clean vector, high resolution"

#### 5.6 `storybook_watercolor` — Storybook watercolor

- **rendering**: "children's picture-book watercolour illustration, soft wet edges,
  visible paper grain, gentle ink linework, hand-painted textures"
- **palette**: primary `["#A7C7E7","#F4A6A6","#C9E4CA"]`, accents `["#6B4F3A"]`;
  palette_line "muted pastels, warm sepia ink, lots of white paper"
- **camera**: "picture-book compositions: wide storytelling frames, characters small in
  big worlds, occasional tender close-ups"
- **lighting**: "soft diffuse daylight, candle or lantern glow at night, no hard shadows"
- **character_design_rules**: "Gentle rounded characters with one identifying colour
  and one signature item (a red scarf, a tiny lantern). Same ink line, same paint
  texture across images."
- **environment_rules**: "cottages, forests, seasides, attics; whimsical but grounded,
  hand-drawn details"
- **negative**: "3D, photorealistic, digital gradients, neon, hard edges, anime"
- **motion_rules**: tier1 `pan_lr` slow ±5 %, `push_in` ≤ 1.06, add `paper_texture`
  overlay option; tier2 suffix "very gentle motion, drifting particles, painterly, no
  morphing"
- **typography**: "Patrick Hand / Caveat Bold", subtitles sepia on translucent paper
  band, highlight `#F4A6A6`
- **audio**: bgm `["lullaby_piano","music_box","gentle_strings"]`, sfx_pack "gentle"
  (page_turn, wind_soft, birds, twinkle, footsteps_grass), voice_direction "warm
  storyteller, unhurried, kind"
- sheet_background "white watercolour paper"; quality_tail "hand-painted, high resolution scan"

#### 5.7 `claymation` — Claymation / stop-motion

- **rendering**: "stop-motion claymation, plasticine characters with visible fingerprints
  and tool marks, miniature handmade sets, felt and cardboard textures, shallow macro
  depth of field, slight frame-to-frame imperfection"
- **palette**: primary `["#E76F51","#2A9D8F","#E9C46A"]`, accents `["#264653","#F4F1DE"]`;
  palette_line "earthy saturated clay colours, warm tungsten set lighting"
- **camera**: "macro lens look, low camera height at character scale, static tripod
  shots, occasional slow slider move"
- **lighting**: "small warm practical lights, soft boxes, visible set edges allowed"
- **character_design_rules**: "Chunky simplified bodies, bead or button eyes, one
  signature accessory sculpted in clay. Same clay colour and finish in every shot."
- **environment_rules**: "handmade miniature sets: cardboard buildings, felt grass,
  painted backdrops, tiny props"
- **negative**: "smooth CGI, photorealistic humans, 2D, anime, perfect symmetry"
- **motion_rules**: tier1 `hold` with `jitter_stopmotion` option (12 fps step, ±1 px)
  and `push_in` ≤ 1.05; tier2 suffix "stop-motion, 12 frames per second feel, handmade
  animation, no smooth CGI motion"
- **typography**: "Chewy / Sniglet", subtitles cream on dark teal band, highlight `#E9C46A`
- **audio**: bgm `["quirky_marimba","toy_orchestra","suspense_pizzicato"]`, sfx_pack
  "foley" (clay_squish, wood_knock, paper_rustle, tiny_bell, footsteps_felt),
  voice_direction "quirky, earnest, slightly deadpan"
- sheet_background "neutral grey card"; quality_tail "macro photography, handmade detail"

`episode_defaults` for the remaining templates: `anime` — hook `shocking_image`,
cliffhanger `cut_to_black`, subtitles `word_pop`; `cinematic_real` — hook `insert_prop`,
cliffhanger `hard_stop`, subtitles `two_line`; `cartoon_flat` — hook `text_overlay`,
cliffhanger `cut_to_black`, subtitles `word_pop`; `storybook_watercolor` — hook
`text_overlay` (the "rule of the day"), cliffhanger `cut_to_black`, subtitles `two_line`;
`claymation` — hook `shocking_image`, cliffhanger `hard_stop`, subtitles `two_line`. All:
`shots_per_scene [2, 4]`, `max_places 2`, `ai_label true`.

Templates not shipped (documented as "add a JSON file"): `fantasy_painterly`, `pixel_art`,
`comic_book`.

---

## 6. Episode template, timing and auto-edit rules

### 6.1 Language

One language per story (`fr` | `en`). Script text, dialogue voices, burned subtitles,
on-screen text and the metadata pack follow it; `title_en`/`hashtags_en` are added for
FR stories. Visuals are language-neutral (no readable text in images — enforced by the
negative prompt). Changing language later = regenerate script + audio only.

### 6.2 Episode template `serial_60s_v1` (data file `templates/episodes/serial_60s_v1.json`)

| Slot | Function | Count | Duration | Rules |
|---|---|---|---|---|
| recap | `recap` (ep ≥ 2) | 0–1 | 2.0–3.0 s | scene `s00`: one line or on-screen text; pays off the previous cliffhanger |
| hook | `hook` | 1 | 1.5–3.5 s | `hook_style` ∈ `insert_prop` (default for fruit_drama: a diegetic object/sign/screen that states the premise — the reference video sells a manor for 1 € in a 0.9 s insert), `shocking_image` (default for family_3d: the strongest image of the episode, no text), `text_overlay` (on-screen ≤ 6 words at 0.0 s). A spoken line or a loud SFX within the first 1.5 s in every variant. |
| body | `setup` / `rising` / `peak` / `turn` | 5–9 | 4.0–8.0 s each (template may raise `peak`/`tender` to 12 s) | **2–4 shots per scene**, minimum shot 0.8 s (reaction cuts); alternate framing (no two consecutive shots with the same framing); at least one "reaction close-up" every 3 scenes; one "dread/quiet" scene without dialogue allowed; escalation: each scene raises stakes or reveals information; ≤ 2 places per episode by default |
| cliffhanger | `cliffhanger` | 1 | 2.0–5.0 s | `cliffhanger_style` ∈ `cut_to_black` (reveal + ≤ 1 line, black at the peak, then the 1.0 s end card `PART {n+1}` / `PARTIE {n+1}` + title) or `hard_stop` (default for fruit_drama: the episode ends mid-beat on the confrontation, end card optional — what the reference video does) |
| total | | 8–12 scenes, 16–30 shots | **55–80 s** target 60 (`serial_60s_v1`); `serial_90s_v1` = 75–100 s | Python enforces; per-slot clamps come from this file (6.4); overflow handling in 6.4 |

Measured on the reference videos (2026-09-25, see `10-REFERENCE-ANALYSIS…`): shot mean
3.2 s / 4.1 s, median 2.5 s / 3.4 s, reaction cuts down to 0.8 s, longest holds 8–11 s;
lines of 3–8 words; 77 s and 99 s episodes; 1–2 places; continuous music/ambience bed
with no silence; both carry an "AI-generated" label; both are true video (Tier 2/3).

### 6.3 Closed lists

- **scene function**: `recap, hook, setup, rising, peak, turn, cliffhanger`
- **framing**: `wide_establishing, medium_single, medium_two_shot, close_up, extreme_close_up, over_shoulder, low_angle, high_angle, insert_prop`
- **camera_motion** (Tier 1 implementable, exactly one per shot): `hold, push_in, pull_out, pan_lr, pan_rl, pan_ud, pan_du`
- **modifiers** (zero or more per shot, layered on the motion): `handheld` (±2 px sinusoidal crop offsets), `jitter_stopmotion` (12 fps stepping, ±1 px)
- **overlays** (per style, applied to every shot): `paper_texture`, `film_grain`, `vignette`
- **transition**: `cut, dissolve(0.3–0.5), fadeblack(0.4), fadewhite(0.3), wipeleft/right(0.3), slideup(0.3)` — grammar: `cut` inside a scene, `dissolve` between scenes of the same place, `fadeblack` when the place changes, `fadeblack` before the cliffhanger card.
- **emotion** (used for both scene `emotion` and line `emotion`; `delivery` is free text): `neutral, happy, angry, shocked, sad, scheming, tension, tender, fear, triumph`
- **hook_style**: `insert_prop, shocking_image, text_overlay`
- **cliffhanger_style**: `cut_to_black, hard_stop`
- **subtitle_mode**: `word_pop, two_line, none`

Where style templates in section 5 say "`handheld` option" or "`jitter_stopmotion`
option" they mean `motion_rules.tier1.modifiers`; "`paper_texture` overlay" means
`motion_rules.tier1.overlays`.

### 6.4 Timing is computed, never guessed

1. Line duration source, in order of truth: (a) real TTS audio → `duration_s` and word
   timestamps from the provider when it returns them; (b) forced alignment of that audio
   through the existing STT chain (`clipping/providers/stt.py`) — **opt-in**, it costs a
   request; (c) when only audio duration is known, words are **evenly split** across the
   line and the UI labels the subtitles "approximate timing"; (d) before any audio exists
   (phase 3 script stage), a deterministic estimate `chars × rate_per_char[language]`
   (constants in `timing.py`, labelled "estimated" in the UI) — superseded as soon as the
   line is synthesised. Nothing here is a silent fallback: the source is stored per line.
2. Scene duration = Σ line durations + `pause_before_first` (0.35 s) + `gap_between_lines`
   (0.25 s) + `tail` (0.6 s; 1.2 s on peak/cliffhanger). Clamp to the **slot's range from
   the episode template** (6.2), not a global range.
3. Episode duration = Σ scenes + transitions − overlaps + end card. If > 75 s: shorten
   tails first, then flag the longest lines for the user ("trim 2 lines to fit"); never
   speed up audio silently. If < 55 s: extend holds on establishing/cliffhanger shots
   (max +1.0 s each) then flag.
4. Subtitles — `subtitle_mode` per style template, user-switchable per story:
   - `word_pop` (default `fruit_drama`, `cartoon_flat`, `anime`): **one word at a time**,
     uppercase, bold italic sans, white with 3 px black outline, centred at 75–80 % of
     height, each word visible for its own duration (from word timestamps; even split
     when only line duration is known), scale pop on entry — what the reference video
     does;
   - `two_line` (default `cinematic_real`, `storybook_watercolor`, `claymation`): one ASS
     `Style` per character (colour accent), karaoke by **per-word Dialogue events** (scale
     pop + colour), not `\k`; max 2 lines, ≤ 32 chars per line, bottom 18 % safe area;
   - `none` (default `family_3d`): no burned dialogue text.
   On-screen hook text (`text_overlay` hook style) is a separate top-third style. An
   optional small **`ai_label`** ("AI-generated", bottom-left, default **on**) is burned
   for platform disclosure.

### 6.5 Render (Tier 1) — pure FFmpeg, built by `render/filtergraph.py`

Per shot (image → clip): `scale=4320:-2` (4× upscale kills zoompan sub-pixel jitter) →
`zoompan=z='<expr>':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=<frames>:s=1080x1920:fps=30`
with eased expressions from `motion.type` → `format=yuv420p`. Modifier `handheld` adds
`crop` with sinusoidal offsets; modifier `jitter_stopmotion` renders at 12 fps then
duplicates frames to 30; overlays (`paper_texture`, `film_grain`, `vignette`) are
blended last from bundled PNG/`noise` filters. Tier ≥ 2 shots that already have an `.mp4`
(and are not `keep_still`) are scaled/padded to 1080×1920, 30 fps CFR, trimmed to
`duration_s`. End card: 1.0 s PNG generated in Python (`PART {n+1}` / `PARTIE {n+1}` +
title, template typography), joined with `fadeblack`.

Sequence: `xfade` between shots per the transitions list (offset arithmetic
`Σ durations − Σ transition durations`, unit-tested), `acrossfade` on audio when a
dissolve is used.

Audio graph: dialogue track = concatenation of line files with `adelay` at computed
offsets; SFX cues `adelay`ed at their anchors; BGM chosen by mood from `assets/bgm/`
(extend the folder with a `bgm_index.json` mapping mood tags → files, CC0/own only),
looped to length, **ducked** under dialogue with
`sidechaincompress=threshold=0.03:ratio=8:attack=20:release=300`, mixed with `amix …
weights=…:normalize=0`; final two-pass `loudnorm I=-14 TP=-1 LRA=11` (reuse
`clipping/loudness.py`). BGM mood = `style_lock.audio.emotion_to_mood[dominant scene
emotion]` (most frequent scene emotion weighted by duration; `default` otherwise).

Encoder: libx264 `-preset medium -crf 20 -pix_fmt yuv420p -movflags +faststart`, AAC
192k; reuse the existing encoder probe memo for NVENC/VideoToolbox when present (opt-in,
because CRF parity across encoders is not guaranteed).

Every command line is written into `render_manifest.json` before it runs; a failed
stage keeps the partial files for inspection.

### 6.6 Re-edit / regenerate per scene (phase 5)

From the episode page: regenerate a shot image (with note), regenerate a line's voice,
change a line's text (re-TTS only that line, recompute timing), swap camera motion,
change a transition. Then **re-render only the affected segment** (the renderer works
per shot and concatenates; a cache keyed by `sha256(inputs + command)` skips untouched
shots) and reassemble.

---

## 7. Curated concept library (10 shipped)

`clipping/aistory/templates/concepts/<id>.json` — each has `title` (fr/en),
`logline` (fr/en), `world`, `cast_sketch` (3–5 with name, role, archetype, one_line,
signature hint), `main_line` (season seed), `hook_formula`, `retention_mechanics`,
`value` (the real substance carried: a dilemma, a lesson, knowledge), `style_fit`
(default template + alternatives), `episode_seed` (episode 1 beat hints),
`content_flags` (violence/romance level). Ship these ten, in both languages:

| id | Title | World & cast | Value | Retention mechanics | Style |
|---|---|---|---|---|---|
| `tentafruit_island` | Tentafruit Island | Reality-show island where fruits compete in couples; a coconut phone delivers votes. Kiwilo (charming schemer), Mangella (proud lead), Broccolia (matriarch host), Pepperino (playboy), Avocardo (heir). | loyalty vs ambition; what people do for approval | eliminations, alliances, betrayals, weekly "who goes home" vote the audience argues about | `fruit_drama` |
| `orchard_inheritance` | The Orchard Inheritance | A dynasty of fruits fights over Grandma Fig's orchard after her will vanishes. | family, greed, forgiveness | secret identities, a will with missing pages, one revelation per episode | `fruit_drama` / `family_3d` |
| `midnight_fridge` | Midnight Fridge | Inside a family fridge, food items live a society; every night something disappears. Detective Pickle, Madame Brie, the twin Yogurts, a paranoid Egg. | trust, scapegoating, evidence over rumour | whodunit with a suspect per episode; the audience guesses | `family_3d` / `fruit_drama` |
| `last_bus_3am` | Last Bus at 3 AM | A night-bus driver in a rainy city picks up passengers who should not exist. Driver Sam, a dispatcher voice, one new passenger per episode. | grief, guilt, letting go | anthology-within-a-serial: each passenger is a mini-mystery; the driver's own secret grows | `cinematic_real` |
| `detective_dawn` | The Detective Who Forgets | An anime detective loses her memory every dawn and reassembles the same case from notes on her arm. Rin (detective), Kaito (partner who may be lying), the Archivist. | memory, identity, who to trust | recurring clue board; the audience knows more than the hero | `anime` |
| `grandmas_rules` | Grandma's Rules | An old storyteller and her grandchild in a watercolour village; each episode a rule of life illustrated by a tale with a twist. | practical wisdom, moral dilemmas | "rule of the day" text hook; the twist ending; a season-long mystery about grandma's past | `storybook_watercolor` |
| `two_minutes_heroes` | Two Minutes to Save the World | Two incompetent superheroes must fix a disaster in two minutes, every day. Captain Obvious, Miss Overthink, their exhausted cat dispatcher. | teamwork, humility, embracing failure | sitcom escalation, running gags, "how did they survive" cliffhangers | `cartoon_flat` |
| `clay_town_confessions` | Clay Town Confessions | Residents of a miniature town confess secrets to a talking mailbox that may be leaking them. Mayor Dough, Baker Pim, the Mailbox, a suspicious pigeon. | privacy, honesty, small-town kindness | interconnected secrets; each confession changes the next episode | `claymation` |
| `the_interview` | The Interview | Candidates interview at a company that asks one impossible moral question; the interviewer is never seen. A new candidate per episode; a receptionist who knows too much. | ethics, "what would you do" dilemmas | the audience answers in comments; the company's purpose is the season mystery | `cinematic_real` |
| `last_ramen_shop` | The Last Ramen Shop | A cosy ramen shop at the end of the world; every customer brings news from outside. Chef Hana, apprentice Bo, a masked regular. | hospitality, hope, community | one customer story per episode; the outside world revealed piece by piece | `anime` / `storybook_watercolor` |

"Generate 10 more" (prompt C1 × 2) uses the chosen style + language + optional user text
and, when present, the import digest (12).

---

## 8. Providers, local hardware, budget

### 8.1 New chains (same shape as `LLM_CHAIN`: ordered `<provider>/<model>`, skip unkeyed links, print every hop, never call an unlisted provider)

| Env / setting | Default (free → paid; paid links only run when `allow_paid` is on) | Notes |
|---|---|---|
| `IMAGE_CHAIN` (text→image, no refs) | `cloudflare/flux-1-schnell, pollinations/flux, local/comfyui, fal/flux-schnell*, openai/gpt-image-2-low*` | `*` = paid |
| `IMAGE_EDIT_CHAIN` (image+refs→image; used for every shot and for sheets 2–3) | `local/comfyui, gemini/nano-banana-2-lite*, fal/seedream-4-edit*, fal/flux-kontext-pro*, gemini/nano-banana-2*` | **No free hosted multi-reference editor exists** (Gemini image models are not on the free tier as of 2026-09-25). Free route = local ComfyUI (FLUX.2 klein 4B / Qwen-Image-Edit). When no link of this chain is runnable, the step **stops and asks**: the user explicitly switches the story to `consistency_mode: prompt_only` (text-only prompts with the locked `prompt_block` + seed reuse via `IMAGE_CHAIN`); every image made that way is labelled "consistency: prompt-only" in the UI and in its JSON. Never switched automatically. |
| `VIDEO_CHAIN` (image→video, tier ≥ 2) | `local/comfyui, fal/seedance-1-pro-fast*, fal/ltx-2-fast*, fal/kling-2.5-turbo-std*, gemini/veo-3.1-lite*` | all hosted links are paid; no recurring free video API exists. Settings and "Test chain" exist from phase 0 (reporting "no adapter yet"); adapters arrive in phase 6. |
| `TTS_CHAIN` | `edge/<voice>, gemini/flash-lite-tts, local/piper, local/kokoro, local/chatterbox` (phase 0); `gcloud/neural2*`, `openai/gpt-4o-mini-tts*`, `elevenlabs/flash*` are **documented extension points**, not shipped defaults | The chain is the **order voices are proposed from** at cast time. A character's voice is pinned (`provider/voice_id`); if that provider fails, the line **fails** and the UI offers "pick an alternate voice for this character" — never a silent switch to another voice. |
| `VISION_CHAIN` (describe frames) | `gemini/flash-lite, openrouter/<free vision model>, local/ollama-vision, gemini/flash*` | reference-video import |
| `LLM_CHAIN` | existing | story writing |
| `LOCAL_COMFYUI_URL`, `LOCAL_OLLAMA_URL` | `http://127.0.0.1:8188`, `http://127.0.0.1:11434` when the backend runs on the host; in Docker use `http://host.docker.internal:8188` (add `extra_hosts: host.docker.internal:host-gateway` to compose) | probed at startup and from Settings |

**Docker and GPUs**: the backend container sees no GPU unless the NVIDIA container
runtime is configured; the hardware probe therefore reports `container: host GPU not
visible` (distinct from `cpu_only`) and the recommended setup for local generation is
ComfyUI/Ollama running **on the host**, reached over `host.docker.internal`. Document both
in `docs/AI_STORY.md`.

Provider adapters live in `clipping/providers/` alongside the LLM ones: `images.py`,
`video.py`, `tts.py` (extend the existing `voiceover.py`/`edge_tts` use, do not fork it),
`vision.py`, `comfyui.py`, `ollama.py`. Each adapter exposes the same small interface
(`generate(request) -> Result` with `est_cost`, `provider`, `model`, `seed`, `paths`) and
a `probe()`; errors reuse `clipping/providers/errors.py` classes so the retired-model
swap (DEC-089) and budget checks apply uniformly. Prices per unit live in
`clipping/providers/pricing.py` with a `PRICES_AS_OF = "2026-09-25"` constant and a test
that every paid link in a default chain has a price.

### 8.2 Hardware profiler (`clipping/aistory/hardware.py`, stdlib only + optional imports)

Order: `pynvml` → `nvidia-smi --query-gpu=name,memory.total,memory.free --format=csv,noheader,nounits`
→ `torch.cuda` if importable → macOS `system_profiler SPDisplaysDataType -json` + MPS →
`rocm-smi --showmeminfo vram --json` → Windows `wmic path win32_VideoController get Name`
(name only; AdapterRAM caps at 4 GB) → RAM via `/proc/meminfo` / `wmic OS` / `sysctl` →
free disk → ComfyUI `GET /system_stats` (authoritative when reachable) → Ollama
`GET /api/tags`. Each probe has a fixture-tested parser. Output `HardwareProfile`
`{gpu_name, vram_gb, ram_gb, disk_free_gb, backend: cuda|mps|rocm|cpu, comfyui: {reachable, models[]}, ollama: {reachable, models[]}}`
→ mapped to a profile:

| profile | condition | local capabilities recommended |
|---|---|---|
| `cpu_only` | no GPU | FFmpeg render, Piper/Kokoro TTS, Ollama small LLM (qwen3:4b) for cheap text steps; all images/video via APIs |
| `low` | < 8 GB VRAM | + SDXL-Turbo / FLUX.2 klein 4B images; LTX 2B short clips |
| `mid` | 8 – < 16 GB | + FLUX schnell fp8 / klein 4B edit; Wan 2.1 1.3B or Wan 2.2 5B (offload) I2V; Chatterbox TTS |
| `high` | 16 – < 24 GB | + FLUX dev fp8, Qwen-Image-Edit GGUF, Wan 2.2 14B GGUF + Lightning |
| `pro` | ≥ 24 GB | + Wan 2.2 14B fp8, LTX-2, HunyuanVideo 1.5 |
| `apple_mps` | Apple Silicon | FLUX schnell via ComfyUI-MPS, Kokoro; no Wan |
| `container_no_gpu` | running in Docker without GPU passthrough | as `cpu_only`, plus the hint to run ComfyUI/Ollama on the host |

The Settings "Local hardware" tab shows the detected profile, the recommended local
models with install hints (ComfyUI workflow templates in
`clipping/aistory/templates/workflows/<profile>/<task>.json`, API-format, placeholder
injection), and a per-task **route** selector: `auto` (local if capable, else free API,
else paid if allowed), `local`, `api`. The story's `generation_profile.route` overrides.

### 8.3 Local generation adapters

- **ComfyUI**: `POST /prompt` with a templated API-format workflow, `WS /ws?clientId=`
  progress → forwarded to the job activity feed, `GET /history/{id}` → `GET /view`.
  Templates shipped: image workflows `t2i_flux2_klein.json`,
  `edit_flux2_klein_multiref.json`, `edit_qwen_image.json` (**phase 0**, so phase 2 can
  use local editing); video workflows `i2v_wan22_5b.json`, `i2v_wan22_14b_lightning.json`,
  `i2v_ltx2.json` (**phase 6**). Missing custom nodes/models → a clear message listing
  what to install, never a silent fallback to the API.
- **Ollama**: registered as an OpenAI-compatible LLM link (`ollama/<model>`) using the
  existing `custom/` mechanism plus `format=<schema>` for structured output.
- **Local TTS**: Piper (6 FR voices, MIT), Kokoro (1 FR voice, Apache-2.0), Chatterbox
  Multilingual (MIT, zero-shot voices → distinct character voices). **Never XTTS**
  (non-commercial licence). Each is an optional extra in `pyproject.toml`
  (`[local-tts]`), probed, never imported at startup.

### 8.4 Free-tier etiquette

Per-provider RPM/RPD limiters in `clipping/providers/limits.py` with the published numbers
(Cloudflare 10k neurons/day, OpenRouter free 50/1000 RPD, Groq 1k RPD…) and a
daily counter persisted in `data/usage.json`; when a limit is near, the chain moves to
the next link and prints why. The story page shows "free budget left today" per chain.

### 8.5 Budget (paid opt-in)

- Settings → Budget: `allow_paid` (default **off**), `per_episode_cap_usd` (default
  **1.00** — the author's ceiling), `daily_cap_usd` (default 3.00), `per_story_cap_usd`
  (default 10.00). Each paid provider key has a `_set` badge (DEC-057).
- **Budget profiles** (data in `clipping/aistory/templates/budget_profiles.json`, chosen
  per story, shown with a live estimate):
  - `free` — $0.00: free image chain or local ComfyUI, Edge/Piper/Kokoro TTS, Tier 1 motion
    on every shot; consistency = local editor or explicit `prompt_only`.
  - `one_dollar` (**default once `allow_paid` is on**) — ≤ $1.00 per episode: every shot
    as an image **with references** on the cheapest editor (≈ 20 shots × $0.03 ≈ $0.60
    when no local editor; $0.00 with one), TTS on the free chain, and the remaining
    budget spent animating the **key shots** in priority order — hook, cliffhanger,
    peak reveals, then the longest dialogue shots — on the cheapest `VIDEO_CHAIN` link
    (≈ $0.11 per 5 s clip today → 3–4 clips without a local editor, 8–9 clips with one).
    The planner picks the number of animated shots so the estimate stays under the cap
    and lists them for approval; the user can swap which shots get animated.
  - `quality` — user-set cap; animate every shot, prefer native-audio models at Tier 3.
  The profile decides *where* the money goes; the caps decide *whether* it is spent.
- Before any step that may spend, the API returns an **estimate** (`est_usd`, breakdown
  per unit) computed from the price table; the UI shows it on the approval button
  ("Generate 12 shots — est. $0.40 on gemini/nano-banana-2-lite, or $0.00 on
  local/comfyui"). Spending above the caps is refused with the numbers.
- `allow_paid`, `per_episode_cap_usd`, `daily_cap_usd`, `per_story_cap_usd` and the
  default budget profile are five-place defaults (config constant, request model,
  `config_adapter`, CLI, Settings form) and get the agreement test from phase 0.
- After each call, the ledger (2.11) is appended; totals are shown per episode and story.

### 8.5.1 `budget_profiles.json` (shipped content)

```json
{
  "$schema": "budget_profiles_v1",
  "profiles": {
    "free":       {"cap_usd": 0.0,  "images": "free_or_local", "tts": "free_or_local", "animate": "none",
                   "consistency": "local_editor_or_prompt_only_opt_in"},
    "one_dollar": {"cap_usd": 1.0,  "images": "cheapest_editor_with_refs", "tts": "free_or_local",
                   "animate": "key_shots_within_cap",
                   "animate_priority": ["hook", "cliffhanger", "peak", "turn", "longest_dialogue"],
                   "video_link_policy": "cheapest_available"},
    "quality":    {"cap_usd": null, "images": "best_editor_with_refs", "tts": "free_or_local",
                   "animate": "all_shots", "video_link_policy": "first_in_chain", "tier3_native_audio": "opt_in"}
  }
}
```
`cap_usd` of a profile is a default for `per_episode_cap_usd`; the user's setting wins.

### 8.7 Chain link → API model id (as of 2026-09-25; verify against each provider's model list at implementation time and record an A-entry per id)

| Chain link | Provider API model id / endpoint | Notes |
|---|---|---|
| `gemini/nano-banana-2-lite` | `gemini-3.1-flash-lite-image` (Gemini API, google-genai SDK or REST) | $0.0336/1K image, up to 14 reference images; not on the free tier |
| `gemini/nano-banana-2` | `gemini-3.1-flash-image` | $0.067/1K |
| `gemini/flash-lite-tts` | `gemini-3.8-flash-lite-tts` (speech generation, 2 speakers max per request) | free tier; if the id has moved, take the current "Flash-Lite TTS" from the pricing page |
| `gemini/flash-lite` (vision + LLM) | current `gemini-*-flash-lite` (same id the repo's `GEMINI_DEFAULT_MODEL` resolves to) | free tier |
| `gemini/veo-3.1-lite` | `veo-3.1-lite-generate-preview` or the current Veo 3.1 Lite id | paid, phase 6 |
| `openai/gpt-image-2-low` | model `gpt-image-2`, `quality: "low"`, size `1024x1536` | ≈ $0.005/image |
| `openai/gpt-4o-mini-tts` | `gpt-4o-mini-tts` | documented extension point only |
| `fal/flux-schnell` | `fal-ai/flux/schnell` | $0.003/MP |
| `fal/seedream-4-edit` | `fal-ai/bytedance/seedream/v4/edit` | $0.03/image, multi-ref |
| `fal/flux-kontext-pro` | `fal-ai/flux-pro/kontext` | $0.04/image, single ref |
| `fal/seedance-1-pro-fast` | `fal-ai/bytedance/seedance/v1/pro/fast/image-to-video` | ≈ $0.11 / 5 s 720p |
| `fal/ltx-2-fast` | `fal-ai/ltxv-2/image-to-video/fast` → migrate to the LTX-2.3 endpoint when it deprecates (announced 2026-08-15) | $0.04/s 1080p incl. audio |
| `fal/kling-2.5-turbo-std` | `fal-ai/kling-video/v2.5-turbo/standard/image-to-video` | $0.21 / 5 s |
| `cloudflare/flux-1-schnell` | `@cf/black-forest-labs/flux-1-schnell` via `POST /accounts/{id}/ai/run/…` | free 10k neurons/day |
| `pollinations/flux` | `https://image.pollinations.ai/prompt/{prompt}?model=flux&width=1080&height=1920&seed=…` (+ API key header) | credits/"pollen" |
| `openrouter/<free vision model>` | pick at implementation from `GET /api/v1/models` filtered `:free` + `image` input modality — candidates today `qwen/qwen3.8-27b:free`, `google/gemma-4-31b:free`; **measure** with `tools/bench_llm.py` before choosing | 50 or 1000 RPD |
| `edge/<voice>` | `edge-tts` voice short names, e.g. `fr-FR-HenriNeural`, `fr-FR-DeniseNeural`, `en-US-GuyNeural` | free |
| `local/piper`, `local/kokoro`, `local/chatterbox` | Python extras `[local-tts]`; Piper voices `fr_FR-tom-medium`, `fr_FR-siwis-medium`, `fr_FR-upmc-medium`…; Kokoro `ff_siwis` (only FR voice) | probed, never imported at startup |
| `local/comfyui` | `POST {LOCAL_COMFYUI_URL}/prompt` with a workflow from `templates/workflows/` | — |
| `local/ollama` / `local/ollama-vision` | `POST {LOCAL_OLLAMA_URL}/api/chat` with `format=<schema>`; vision model e.g. `gemma3:4b` / `qwen2.5vl:7b` | — |

ComfyUI workflow templates (8.3) are **authored by the implementer** from ComfyUI's own
default graphs for each model (export "API format", replace the literal inputs with
`{{prompt}}`, `{{negative}}`, `{{seed}}`, `{{image_path}}`, `{{ref_paths}}`, `{{width}}`,
`{{height}}`, `{{frames}}`, `{{fps}}`), and validated at runtime against `GET /object_info`;
the spec deliberately does not freeze node graphs, only the placeholder contract and the
model files each template needs (listed in the template's `requires` field: e.g.
`flux2-klein-4b.safetensors`, `qwen_image_edit_2509_q4.gguf`, `wan2.2_ti2v_5B_fp8.safetensors`,
`ltx-2-13b.safetensors`).

### 8.6 Settings additions

Tabs *Generation*, *Local hardware*, *Budget*; every new key persisted through the
existing `PERSISTED_KEYS` mechanism (0600 file, empty value clears), badge per secret,
"Test chain" button per chain reusing the `diagnose_chain` UX.

---

## 9. Jobs, API and worker

### 9.1 Story jobs

Reuse the job store/worker (`web/api/store`, `worker.py`) with a new `kind` field:
`"clip"` (default, unchanged) or `"story_step"`. A story-step job carries
`{story_id, ep, step, params}` and runs `clipping/aistory/steps/<step>.py::run(ctx)`,
which prints progress lines the same way (`activity.py` tee), honours `cfg.cancel_token`,
and ends in `awaiting_approval` or `failed`. `awaiting_approval` is **terminal for the
worker** (the slot is freed, it does not count against `MAX_CONCURRENT_JOBS=1`) but not
for the user: `/approve/...` flips the job to `done`, a regenerate creates a new job. The
job list groups story-step jobs under their story. `fail_stale_jobs` must **not** touch
`awaiting_approval` jobs at restart (record a DEC; it already mishandles `needs_upload`,
which is a known follow-up). Step request body: `{ep?: int, params?: object}`.

Steps (the `{step}` values): `concepts, bible, style, cast, places, season, script,
storyboard, assets, render, metadata, memory, feedback, propose-next, rerender,
fast-track, import`.

### 9.2 Endpoints (all behind the bearer token; media through the existing signed URLs)

```
GET    /api/stories                          list (index)                                        [phase 1]
POST   /api/stories                          create draft {language, seed_text?, style_template_id?, generation_profile?}  [1]
GET    /api/stories/{id}                     bible + status + cost totals + hardware/route summary [1]
PATCH  /api/stories/{id}                     edit bible fields, narrator, generation_profile       [1]
DELETE /api/stories/{id}                     delete (same rules as job delete; frees disk)        [1]
GET    /api/stories/{id}/concepts            library (filtered by language/style)                 [1]
POST   /api/stories/{id}/concepts/generate   → job(step="concepts")                               [1]
POST   /api/stories/{id}/concepts/choose     {concept_id | concept payload}                       [1]
POST   /api/stories/{id}/steps/{step}        run a step → job id; body {ep?, params?}             [1+, steps listed in 9.1]
POST   /api/stories/{id}/approve/{doc}       doc grammar below                                    [1+]
POST   /api/stories/{id}/regenerate          {target, note?} — target grammar below               [1+]
GET    /api/stories/{id}/estimate/{step}     est_usd + units + route (+ ep for episode steps)     [1+]
POST   /api/stories/{id}/characters/{cid}/uploads   reference images (unique names, size limits)  [2]
GET    /api/stories/{id}/episodes/{ep}       script + storyboard + assets + manifest + metadata   [3+]
PATCH  /api/stories/{id}/episodes/{ep}/script      inline edits (lines, on-screen text)           [3]
PATCH  /api/stories/{id}/episodes/{ep}/storyboard  inline edits (prompts, motion, transitions)    [3]
POST   /api/stories/{id}/episodes/{ep}/feedback    paste audience feedback → job(step="feedback") [5]
POST   /api/stories/{id}/imports              upload reference video → job(step="import")         [7]
POST   /api/stories/{id}/imports/{iid}/{action}   action: use-style | remix-premise | mirror-cast  [7]
GET    /api/hardware                          detected profile + recommendations                  [0]
GET    /api/settings/chains/{name}/test       diagnose a chain                                    [0]
GET    /api/stories/{id}/export               zip bundle                                          [5, optional]
POST   /api/stories/import                    import a bundle                                     [5, optional]
```

**Approve `doc` grammar**: `bible | style | character:<cid> | place:<pid> | prop:<pid> |
season | script:<ep> | storyboard:<ep> | assets:<ep>`.

**Regenerate `target` grammar** (one grammar, used by every phase; `<sid>` = scene id
`sNN`, `<shid>` = shot id `shNN`, `<lid>` = line id `lNN`):

```
bible:<field>                       field ∈ logline|premise|tone|world|themes   (B1/B2/B3 again)
concepts                            (C1 again, "10 more")
character:<cid>:text                (K1 again)
character:<cid>:image:<portrait|turnaround|expressions|extra:<n>>
character:<cid>:voice               (new voice proposal + sample)
place:<pid>:text | place:<pid>:image:<variant>
prop:<pid>:text  | prop:<pid>:image
season:<ep>                         (S2 again for one arc entry)
scene:<ep>:<sid>                    (E2 again; for recap/hook/cliffhanger scenes → E3 again)
hook:<ep> | cliffhanger:<ep> | teaser:<ep>   (E3 partial)
shot:<ep>:<shid>:plan               (T1 again)
shot:<ep>:<shid>                    (image again)
shot:<ep>:<shid>:video              (I2V again, phase 6)
line:<ep>:<lid>                     (voice again)
metadata:<ep>:<platform>            (M1 again)
```

Request models in `web/api/models.py` (pydantic, `model_fields_set` for "not sent"),
contract-tested like `test_dashboard_payload_contract.py`.

### 9.3 CLI

`main.py --ai-story` subcommands mirroring the steps for scripting/tests:
`--ai-story new --lang fr --concept tentafruit_island --style fruit_drama`,
`--ai-story step <story_id> <step> [--ep 1] [--auto-approve]`, `--ai-story render …`,
`--ai-story fast-track <story_id> --ep 1`. Same defaults as the API (five-place test).

---

## 10. Dashboard (React/Vite, `web/dashboard/src/pages/story/*`)

- **StoriesList** — cards (cover, title, style badge, language, episodes done, cost
  total, status), "New story".
- **NewStoryWizard** — steps 1–7 of section 3 as a vertical stepper; each step shows the
  generated items as editable cards with *Regenerate (with note)*, *Lock*, *Approve*;
  the estimate chip on every generating button; the route chip (local / free / paid).
- **CastEditor** — character card: portrait, turnaround, expressions (click to
  regenerate one), descriptor/signature items/personality fields, voice picker with
  "play sample", upload reference images (drag-drop, stored in `refs/uploads/`, used as
  extra references), relationships.
- **PlacesProps** — same pattern; time-variant chips.
- **SeasonBoard** — arc timeline; series memory panel (recaps, open hooks, relationship
  state); feedback paste box.
- **EpisodeStudio** — three panes on desktop / tabs on phone: *Script* (scene list,
  inline line editing, consistency issues), *Storyboard* (shot grid: image, framing,
  motion, prompt accordion, regenerate/lock), *Preview* (rendered video, subtitles
  toggle, metadata pack, cost ledger, download).
- **Settings** tabs (8.6) and **Hardware** panel.
- All pages responsive at 375/820/1280 px; SSE activity feed reused from JobDetail.

---

## 11. Audio assets

- `assets/bgm/bgm_index.json`: `{file, moods[], bpm, licence, source}`; only CC0 or
  self-made files are committed; a Settings toggle enables Freesound (API key, CC0
  filter only, 24 h cache) for more.
- `assets/sfx/<pack>/…` small CC0 packs for `soap, cartoon_soft, anime, real, cartoon,
  gentle, foley` (the cue names used in 5.x), with `sfx_index.json`. Missing cue → the
  cue is skipped and reported, never a crash.
- Voices: a curated `voices.json` per provider with `{voice_id, lang, gender, age,
  style_tags}`; the cast step proposes one distinct voice per character (no two lead
  characters share a voice) and the user can change it.

---

## 12. Reference-video import (phase 7, also the tool used to analyse the inspiration videos)

`tools/analyze_reference.py` + step `import`:

1. Probe (`ffprobe`), extract audio → existing STT chain → transcript.
2. Scene cuts via `ffmpeg -vf select='gt(scene,0.3)',showinfo` → cut list, shot
   lengths, count.
3. Sample 1 frame per cut (max 24) → contact sheet(s) → VISION_CHAIN prompt V1 →
   style fragments (rendering, palette hexes, lighting, character design language).
4. Transcript + frame descriptions → V2 → premise skeleton, conflict, archetypes.
5. Output `imports/<id>/analysis.json` (+ frames), shown as a card: "Use style" (creates
   `styles/custom_<iid>.json` in the story folder with `template_id: "custom:<iid>"`,
   `version: 1`, by merging V1 fragments into the closest shipped template; editable;
   then the normal style-lock step), "Remix premise" (feeds C1 as seed; the concept gets
   `concept_id: "import:<iid>"`), "Mirror cast dynamic" (archetypes feed K1 as hints).
   Structure/pacing statistics are recorded in the analysis for information (the user
   did not ask to turn them into templates, but they validate the A-entries of 6.2).

Nothing is downloaded from platforms; the user uploads the file (same policy as clips).

---

## 13. Testing and acceptance

- Unit (stdlib + pytest): schema validation of every document; `prompt_block` assembly;
  prompt builders (golden strings); timing math (6.4) incl. overflow/underflow; xfade
  offset arithmetic; filtergraph string builder (golden command lines); hardware parsers
  on fixtures (nvidia-smi csv, system_profiler json, rocm json, wmic text, ComfyUI
  system_stats); price table completeness; budget refusal; limiter counters; chain
  parsing for the new chains; five-place default agreement; branding guard; payload
  contract for every new endpoint; `awaiting_approval` survives restart; delete frees
  only the story's folder.
- Render golden: a fixture episode with 3 tiny synthetic PNGs + 3 short silent WAVs
  renders under 10 s; the test asserts duration ± 0.1 s, resolution, fps, and that
  `render_manifest.json` lists every command. framemd5 of that fixture is recorded once
  and compared (this is the AI-Story equivalent of the clip renderer's parity rule).
  **Policy**: ffmpeg is a requirement of the CI job (install it in the workflow, as the
  clip tests already assume it); the golden test never skips. The filtergraph *builder*
  tests (golden command strings) need no ffmpeg at all and are the bulk of the coverage.
- Tier-2 (human-acked): one real story created from `tentafruit_island` in FR, cast of
  3, one 60-s episode rendered on the free route (and once on local ComfyUI if a GPU is
  present), watched on a phone; one paid estimate shown and refused by the cap; one
  regenerate-shot + partial re-render.

---

## 14. Assumptions to register on day one (A-030 …)

- A: Gemini image models have no free tier (checked 2026-09-25); revisit monthly.
- A: Cloudflare Workers AI free allocation (~10k neurons/day) yields ≈ 170 schnell images/day.
- A: Edge TTS remains usable without a key (unofficial); Piper is the FR fallback.
- A (measured 2026-09-25 on two reference videos, register as *Confirmed* with the
  analysis file as evidence): shot mean 3.2–4.1 s, reaction cuts ≥ 0.8 s, lines of 3–8
  words, 1–2 places per episode, continuous music bed, single-word pop captions in the
  fruit-drama genre, no narrator. *Invalidated*: "hook text overlay in the first 1.5 s"
  (the hook is a diegetic insert or a shocking image) and "cut-to-black is the only
  cliffhanger" (hard stop mid-beat is common). Two videos is a small sample — the phase-7
  analyzer re-measures every import and appends to these entries.
- A: Free tiers' RPD (Gemini Flash-Lite ~1000, Groq 1k, OpenRouter free 50/1000) are
  enough for one episode's ≈ 25 LLM calls.
- A: Prompt-only consistency (locked prompt_block + seed) is acceptable as a labelled
  degraded mode when no editor with references is available.
- A: `MAX_CONCURRENT_JOBS=1` is acceptable for story steps in v1.

---

## 15. Phase map (each has its own prompt file in this folder)

| Phase | File | Delivers | Ends MVP? |
|---|---|---|---|
| 0 | `01-phase-0-rename-shell-providers.md` | rename, mode switch, chains + adapters (image/edit/tts/vision), hardware profiler, budget, settings, pricing, limiters | |
| 1 | `02-phase-1-workspace-concepts-bible-style.md` | story store, index, steps 1–4, concept library, style templates, wizard pages | |
| 2 | `03-phase-2-cast-places-season.md` | steps 5–7: characters (sheets, voices), places, props, season arc, series memory | |
| 3 | `04-phase-3-episode-writer.md` | steps 8–9: script (E1–E4), storyboard (T1), timing, consistency check, EpisodeStudio script/storyboard panes | |
| 4 | `05-phase-4-assets-render-metadata.md` | step 10–12: asset generation, Tier-1 renderer, subtitles, BGM/SFX, metadata pack, preview pane, CLI fast-track | **MVP** (2 styles: fruit_drama, family_3d) |
| 5 | `06-phase-5-series-reedit-styles.md` | step 13 (memory, feedback, new characters/twists), per-scene re-edit + partial re-render, remaining 5 styles; export/import bundle **optional** (not a user requirement, may be deferred) | |
| 6 | `07-phase-6-video-tiers-local.md` | Tier 2/3: VIDEO_CHAIN adapters, ComfyUI workflows (Wan/LTX), I2V per shot, paid estimates end-to-end | |
| 7 | `08-phase-7-reference-import.md` | reference-video import (12), custom style from video, remix, archetypes | |

Each phase: its own plan file, checkpoint commit, stages, DEC/A entries, Tier-1 + Tier-2
with human ack, `docs/AI_STORY.md` updated, VISION "Where it stands" updated.
