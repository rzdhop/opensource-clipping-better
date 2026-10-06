# Plan 32 — The fruit drama product: one recipe, one chat flow, the worker's voices and mouths

Date: 2026-10-06. Base: `feat/comfy-tts-chatterbox` (plan 31, pushed; merge order 31 then 32).
Branch: `feat/fruit-drama-product`. Status: PLAN, awaiting the human's answers and "Go".

## Goal

The human (2026-10-06): "fait moi le produit parfait, utilisable via le MCP facilement avec Claude, édite
les prompts, les idées de noms etc, une vraie énorme amélioration très lourde". Read with the research
report (`reports/Fruit drama viraux en self hosted.md`): a fruit drama series made from a chat in a
handful of tool calls, with the viral recipe baked in (names, cast, beats, cliffhanger, Team question,
end card, Pixar-style look, no sexist tropes), the characters' voices frozen and cloned on the worker,
and, if the live test says yes, the mouths moving from the voice.

## What the three maps found (the facts the plan rests on)

- From the chat a story cannot pass the bible: no `style` step is exposed (`mcp_server/director.py:46-52`),
  so cast/places refuse "Approve the style first"; the fruit-drama skill abandons the pipeline and drives the
  GPU by hand, with a shell. `fast-track` / `story-fast-track` exist (`steps/__init__.py:139,144`, take
  `runner=`) but are not exposed. `story_create` with no profile makes a tier-1 legacy story (no clips);
  the app's profile builder `media_policy.new_story_profile` is only called by the CLI. No estimate tool;
  `workflow.*_estimate` exist unexposed. Step params undocumented. `comfy_download` ≤ 50 MiB vs a 40 MB
  episode. Runs are in memory (a restart loses parked prompts).
- The genre has no record: `fruit_drama` is a style JSON + default universe + a suggested format. No naming
  rule anywhere; cast 3–5 from the concept; the end card is only rendered on `cut_to_black` (fruit_drama is
  `hard_stop`, so "Partie N demain" never shows); the hook's on-screen text is required by J1 but only burned
  for `text_overlay` (fruit_drama is `insert_prop`); the look contradicts itself (JSON "photorealistic …
  Octane", send layer "Pixar-style cartoon"). Shots are clamped to 5–10 s (`native_speech.SHOT_WINDOW_S`),
  1–4 lines a shot. Judges J2/J3 hard-code "whole fruit head at human head scale". Goldens pin every prompt
  (re-pin with a dated comment, the documented convention); `fruit_drama.json` prose must match
  `00-MASTER-SPEC.md` §5.1 verbatim (`tests/test_aistory_templates.py:263-305`).
- Voices: TTS providers are `edge, gemini, local, gcloud, openai, elevenlabs` — no `runpod`
  (`providers/generation.py:228`); `voices.KEPT_EXTENSIONS = (mp3, wav)`; lipsync is fal/kling only and
  refuses fruit heads. Wan2.2-S2V (image + voice → talking clip) runs on CORE nodes of the worker's ComfyUI
  0.34.0; weights ≈ 16.4 GB fp8 + wav2vec2 0.63 GB + the umt5/vae already on the volume; the worker's
  `extra_model_paths.yaml` lacks `audio_encoders` (a symlink in our image fixes it); 77-frame chunks = 5.0 s
  at 16 fps; NO measured timing, NO evidence on cartoon faces, NO evidence on French; InfiniteTalk is also
  core (v0.11+) as the fallback.

## Decisions proposed (DEC-315 once approved)

1. **A genre recipe record**: `clipping/aistory/templates/recipes/fruit_drama.json` — the naming rule
   (telenovela "-ito/-ita" French puns on the species: Fraisita, Bananito, Citronello, Avocadina…; no brands),
   the fixed cast (5–8 recurring, roles), the beats (recap ≤ 6 words from ep 2 → confrontation → peak →
   cliffhanger ≤ 40 words), the "Team X ?" closing question, the end-card text ("Partie N demain"), the voice
   direction, the content guardrails (no sexist/racist tropes, no sexualisation; the lesson of the report),
   the posting cadence note. Injected into the setup block, C1v2's cast ask, E1v3's shape line, E3's asks,
   M1's pinned comment; stored on the story at create (`recipe: fruit_drama`) so existing stories are
   byte-identical (gate = the story's recipe field).
2. **One look**: Pixar-style 3D cartoon everywhere — `fruit_drama.json` rendering/negative/design rules and
   §5.1 of the master spec edited together; the judges' "whole fruit head at human head scale" kept; locked
   styles untouched (new stories only).
3. **A format of its own**: `templates/episodes/fruit_drama_75s_v2.json` — window 60–90 s, 4–6 scenes of
   1–2 shots at 5–10 s, one silent reaction shot, recap from ep 2, cliffhanger, end card 1.5 s with CTA +
   the Team question; the render draws the end card and the hook's on-screen text when the recipe says so
   (render goldens re-recorded with `tools/render_golden.py --record`).
4. **The chat flow**: `story_create(..., preset="fruit_drama")` builds the full profile (v2, tier 3, api,
   references, `own_gpu`, universe fruits, recipe, format); the `style` step and `story-fast-track` /
   `fast-track` exposed as steps (`story_make_episode` = fast-track for episode N; Claude still writes every
   prompt through the chat runner, but in ONE run instead of eight starts); `story_estimate(story_id, what)`;
   `episode_sheet(story_id, ep)` (every shot's keyframe/clip frame on one contact sheet);
   `episode_export(story_id, ep, max_mib)` (a share copy under the download ceiling); step params and
   approvals documented in the docstrings; `story_options` lists presets, profiles and formats; the
   fruit-drama skill rewritten against the new tools and kept in the repo (`.claude/skills/…`).
5. **Frozen voices on the worker**: a `runpod` TTS provider on `tts_chatterbox` (plan 31's route); the cast
   step makes ONE reference per character once (a Gemini prebuilt voice picked by the writer from the
   catalogue, ≈ 12 s of in-character French, kept as the character's `voice_reference.wav`), every line is
   cloned from it with a fixed seed; `own_gpu`'s TTS chain starts with `runpod/tts_chatterbox`; a price row;
   FLAC→WAV at the adapter.
6. **Talking clips as a tested bet**: the `s2v_wan22` template (one 5-s chunk, core nodes) + the
   `audio_encoders` symlink in `docker/worker-comfyui-tts`; the human runs ONE paid line on a fruit keyframe
   (≈ $0.10–0.30, estimate — unverified); only then the clips step learns `talking_clips: runpod_s2v`
   (speaking shots → S2V with the shot's dialogue track, silent shots → i2v). Fallback: InfiniteTalk (core).
7. **Scope kept out**: publishing automation (Postiz/YouTube API) — a plan of its own; the dashboard UI
   (the product is the chat); MiniMax H3 (EU-excluded licence).

## Stages (one commit each; Tier 1 selection per stage in both envs; CI full suite at every push)

0. CHECKPOINT — branch from plan 31, baseline on the touched-area selection.
1. **MCP unblock** (Opus: cross-cutting on director/story_tools): `style` step; `fast-track` +
   `story-fast-track` steps; `story_estimate`; `story_create(preset=…)` + `story_options` presets/profiles;
   docstrings with every step param. Tests in `test_mcp_server.py` style. Risk: medium. Contract: the 20
   story tools' names, the concepts test, the director tests.
2. **The recipe record + writers** (Opus: prompt surgery under goldens): the JSON, `context.setup_context`
   RECIPE block, C1v2 names ask, E1v3 shape, E3 asks, M1 comment, the validator for names; gated on the
   story's `recipe`. Goldens unchanged for recipe-less stories (the guard); new goldens for the recipe.
   Risk: high (budgets: re-measure `SETUP_INPUT_BUDGET`/`INPUT_BUDGET`).
3. **The look** (Sonnet): `fruit_drama.json` + spec §5.1 + `_FRUIT_PEOPLE` reconciled; `test_aistory_prompting`
   goldens re-pinned with a dated comment; `test_story_body_rule` kept. Risk: medium.
4. **The format + render** (Sonnet): `fruit_drama_75s_v2.json`; end card on `hard_stop` when the recipe
   asks; hook text burned for `insert_prop` when the recipe asks; `format_fit`, `timing` tests; render
   goldens re-recorded. Risk: medium (render goldens are ffmpeg-keyed, DEC-156).
5. **Episode sheet + export** (Sonnet): `episode_sheet`, `episode_export(max_mib)` (ffmpeg CRF ladder
   until under the ceiling), tests. Risk: low.
6. **Voices on the worker** (Opus: provider + cast step): `runpod` TTS adapter on `tts_chatterbox`,
   `KEPT_EXTENSIONS`, the reference made at cast time, the `own_gpu` chain, pricing, tests
   (`test_story_voices*`, `test_runpod_*` style). Risk: high (the cast step's voice pinning; DEC-281
   consent rule: synthetic references only).
7. **S2V template + worker symlink** (Sonnet): `s2v_wan22.json`, the Dockerfile line, `requires`, frame
   rule (fixed 77-chunk), tests. Then the human's live test (gate). Risk: the bet.
8. **S2V in the clips step** (Opus; only after the gate): `talking_clips` policy, `GenRequest` audio
   field, the video link table, pricing, tests. Risk: high.
9. **The skill + docs + close-out** (Sonnet/Haiku): `.claude/skills/fruit-drama-episode/SKILL.md` rewritten
   (and the plugin copy), `docs/MCP.md`, `docs/AI_STORY.md`, CHANGELOG, DEC-315, A-, CHECKPOINT, PR.

## Rejected alternatives

- Rewriting the skill alone (no code): the pipeline is dead at the style step and needs a shell — no.
- A separate "fruit drama" engine beside the story engine: duplicates writers, judges, render; the recipe
  record reuses everything.
- Hosted lip-sync (Kling on fal): refuses fruit heads today; S2V/InfiniteTalk run on our worker.
- MiniMax H3 for talking clips: EU-excluded licence.
- Shots of 3 s like the viral series: the engine's 5–10 s clamp (DEC-304) and the clip links' 5-s floor;
  the recipe uses cuts inside 5-s clips (reaction shots) instead.

## DECISIONS.md check

Touches DEC-268 (a style only suggests a format — kept: the preset chooses, the story keeps its own),
DEC-303 (send layer, hashes untouched — the recipe adds a section, cores untouched), DEC-304 (5–10 s shots —
kept), DEC-305/306/307/311 (gates — kept; the end card and hook text are render-side), DEC-281 (voice
references need consent for real people — synthetic only here), DEC-310/312/313/314 (the worker and MCP —
extended). No conflicts.

## Riskiest stage

Stage 2 (the recipe inside the writers under forty goldens and token budgets) for the code; stage 7's live
test for the product (nothing proves S2V moves a fruit's mouth in French).

## Cost of an episode on `own_gpu` after the plan (estimate)

6 keyframes × $0.015 + 6 clips × $0.03–0.10 + 18 lines × $0.004 ≈ $0.35–0.80; S2V clips unknown until
measured; the $2 cap stays.
