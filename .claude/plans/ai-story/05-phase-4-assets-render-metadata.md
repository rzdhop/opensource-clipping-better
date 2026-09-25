# Phase 4 prompt — assets, Tier-1 renderer, subtitles, audio mix, metadata pack (MVP)

Paste this into Claude Code from the repository root. Phase 3 merged and acknowledged.
This phase completes the MVP: bible → Tier-1 episode, styles `fruit_drama` and
`family_3d` exercised end-to-end.

---

Continue under the repository protocol: read `.claude/VISION.md`, `.claude/DECISIONS.md`,
`.claude/ASSUMPTIONS.md`, `.claude/CHECKPOINT.md`, then
`.claude/plans/ai-story/00-MASTER-SPEC.md` in full. Follow spec section 0. The renderer
built here is **new code** under `clipping/aistory/render/`; it must not modify
`clipping/studio/` (if you need a helper from there, import it — and if you change it,
the frame-parity proof on a real clip render is owed).

## Goal of this phase

Workflow steps 10–12 (spec section 3) and the CLI fast-track.

1. **Asset generation** (step 10):
   - Images per shot through `IMAGE_EDIT_CHAIN` with the shot's `reference_images`
     (character sheets + master plate + props), or `IMAGE_CHAIN` with the locked
     prompt + `ref_seed` in prompt-only mode (labelled). Store `seed, provider, model`
     per shot; each shot regenerable with a note; lock per shot.
   - Voice per line through the character's pinned `voice` (provider/voice_id/rate/pitch);
     a failing provider fails the line and offers an alternate voice — no silent switch
     (spec 8.1). Word timestamps per the spec 6.4 order of truth: provider timestamps →
     forced alignment through the existing STT chain (**opt-in**) → even split labelled
     "approximate timing"; the source is stored per line. Audio saved as
     `voice/line_NN.mp3` + `.json`; timing engine re-run with real durations (the phase-3
     estimates are superseded).
   - SFX/BGM resolution (spec 11): `assets/sfx/<pack>/` CC0 packs with `sfx_index.json`
     covering every cue name listed in the seven templates' `audio.sfx_cues` (a test
     checks the union; ship small files; missing cue → skipped and reported),
     `assets/bgm/bgm_index.json` (moods → files; extend the existing folder; CC0/own only),
     BGM chosen through `style_lock.audio.emotion_to_mood` from the dominant scene emotion
     (spec 6.5, tested).
   - Estimates before the step (images × price + TTS chars × price per route); caps
     enforced; story-level ledger appended per call with `ep`; the asset grid shows route
     and consistency label.
2. **Renderer** `clipping/aistory/render/` (spec 6.5), pure FFmpeg command lines built in
   Python (`filtergraph.py` with golden tests):
   - per-shot clip: 4× upscale → eased `zoompan` per `motion.type` (all closed-list
     motions), modifiers `handheld` (crop offsets) and `jitter_stopmotion` (12 fps
     stepping), overlays `paper_texture`/`film_grain`/`vignette` → 1080×1920, 30 fps CFR,
     yuv420p; Tier ≥ 2 `.mp4` inputs scaled/padded/trimmed (only a golden test with a
     synthetic clip here; real I2V is phase 6);
   - sequence: `xfade` per the transitions list with the tested offset arithmetic,
     `acrossfade` on dissolves; ending per `cliffhanger_style`: `cut_to_black` → black +
     1.0 s end card PNG generated in Python (`PART {n+1}` / `PARTIE {n+1}` by language +
     title, style typography); `hard_stop` → the last shot ends the file (end card
     optional); the BGM/ambience bed runs under the whole episode with no gap;
   - subtitles per `subtitle_mode` (spec 6.4): `word_pop` (one uppercase word at a time,
     centred at 75–80 % height, scale pop, per-word timing or even split), `two_line`
     (ASS `Style` per character, per-word Dialogue events, ≤ 2 lines × 32 chars, bottom
     18 %), `none`; hook on-screen text as a top-third style at 0.0 s when
     `hook_style = text_overlay`; the small `ai_label` overlay (default on); fonts from
     `custom_fonts/` via the existing font resolution (`clipping/fonts.py`);
   - audio: dialogue `adelay` at computed offsets, SFX `adelay` at anchors, BGM looped,
     ducked with `sidechaincompress` (spec values), `amix … normalize=0` with weights,
     two-pass `loudnorm I=-14 TP=-1 LRA=11` reusing `clipping/loudness.py`;
   - encoder: libx264 crf 20 medium + AAC 192k, faststart; hardware encoder opt-in via the
     existing probe memo;
   - `render_manifest.json` (spec 2.9) written **before** each command runs, with sha256
     of every input, every command line, output duration, loudness, framemd5;
   - per-shot cache keyed by `sha256(inputs + command)` so later re-renders skip untouched
     shots (used by phase 5).
3. **Golden render test** (spec 13): fixture of 3 tiny PNGs + 3 short silent WAVs renders
   in < 10 s; asserts duration ± 0.1 s, resolution, fps, manifest completeness; framemd5
   recorded once in the fixture and compared. **ffmpeg is a CI requirement** (add it to
   the workflow if the clip tests do not already install it); the test never skips. The
   filtergraph builder's golden-string tests need no ffmpeg and carry most coverage.
4. **Metadata pack** (step 12, spec 2.10): prompt **M1** per platform (tiktok, shorts,
   reels) in the story language + EN fields for FR stories; `description` ends with the
   `next_episode_teaser` and `pinned_comment` carries it with the "PART n+1" call; cover
   = the hook shot with the hook text baked at template typography (PIL is already a
   dependency of the studio; if you use it, import late).
5. **Endpoints** (spec 9.2): `/steps/assets|render|metadata|fast-track`,
   `/approve/assets:<ep>`, `/regenerate` targets `shot:<ep>:<shid>` (image),
   `line:<ep>:<lid>` (voice), `metadata:<ep>:<platform>`; `GET /episodes/{ep}` now
   including assets/manifest/metadata; media via the existing signed URLs; download of
   `episode_final.mp4`.
6. **Fast track** (spec 3): `POST /steps/fast-track` and CLI `--ai-story fast-track <id>
   --ep N` chaining script → storyboard → assets → render → metadata with auto-approval,
   **stopping before any paid spending** unless the caps allow it; progress in the
   activity feed; cancellable.
7. **Dashboard** (spec 10): `EpisodeStudio` *Storyboard* pane now shows images with
   regenerate/lock, *Preview* pane (video player, subtitles toggle, metadata pack with
   copy buttons, cost ledger, download), "Fast track" button with the total estimate.
   Phone layout checked; the video plays on the phone over the tailnet.
8. **Docs + decisions**: `docs/AI_STORY.md` steps 10–12 and fast-track, README updated
   ("MVP: AI Story"), DEC for "AI-Story renderer is pure FFmpeg, separate from the clip
   studio, with its own golden-render parity rule", DEC for the audio-mix constants, A-
   entries for measured render times on the VPS.

## Out of scope

Tier 2/3 video, per-scene partial re-render UI (the cache is built now, the UI comes in
phase 5), series memory update, remaining 5 styles (their JSON exists since phase 1 but
is not exercised in Tier-2 here), imports.

## Before you plan

Ask only what remains open. Propose stages (riskiest: the filtergraph/xfade/audio
graph), regression contract (phases 0–3, clip renderers untouched — prove with the
existing framemd5 expectations), expected DEC/A, and the Tier-2 script: render episode 1
of the FR `tentafruit_island` story on the free route (Edge TTS + free images or local
ComfyUI), watch it on the phone, check subtitles per character, ducking, loudness,
cliffhanger cut-to-black + end card, metadata pack; then create an EN `family_3d`
story with fast-track and render episode 1; record durations and costs.

## Acceptance

- Tier-1 green in both envs; new tests shown failing pre-change (filtergraph golden
  lines, xfade offsets, ASS builder, ducking graph, manifest-before-run, cache key,
  M1 golden string, fast-track stops at paid spending).
- Two real episodes rendered (55–75 s, 1080×1920, 30 fps, −14 LUFS ± 1) and watched.
- Every spending call is in `cost_ledger.json`; free-route episode cost is $0.00.
