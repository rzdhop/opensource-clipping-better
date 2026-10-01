# AI Story phase 7 — the quality overhaul — staged plan (APPROVED 2026-10-01, with the three go-items accepted as recommended)

Session 2026-10-01. Repo protocol FULL task. Direction DEC-219. Brief `.claude/plans/ai-story/15-phase-7-quality-overhaul.md`.
Base: `main` = 30604dd. Worktree `.claude/worktrees/ai-story-phase-7` on `feat/ai-story-phase-7`, created at CHECKPOINT after
approval; the main checkout's branch is never switched (the `rzc-backend` container bind-mounts it). Ids: DEC-220+, A-110+.

## Context

After watching story A `979c8376e43e` and story B `04feb539840f` ep 1 the human gave the verdict in DEC-219: only one shot
moves, the story cannot be followed, the free image links look bad, the prompts carry no context. EXPLORE (appendix) found
why: the shot's own content is 7.6 % of a 190–280-word image prompt placed after the descriptors; the clip prompt is 24–28
words of raw `@char`/`#place` tags; the first free image link sends no size, seed or negative, and prompt-only mode reuses
one seed per lead so pollinations returned the same picture for five of A's shots; the episode's object (the toaster, the
key) is never an entity, so it is never drawn; personality, relationships, voice direction, line delivery and the
turnaround/expression sheets are written and never read by any call; A's climax line is spoken twice verbatim and B ships
9.9 s short with only a warning. Phase 7 fixes the defects, moves cast/places/props/keyframes to quality models under a
preset, layers the prompts on a structured look, animates every shot as 6–10 beat clips on seedance, pre-writes an
approved knowledge base with a context builder, and gates the story with a judge and a hard length window. Target cost
≈ $1.50–1.75 per episode under caps $2 / $6 / $20.

## The human's CLARIFY answers (binding)

| # | Topic | Answer |
|---|---|---|
| 1 | Budget | ≈ $1.50 per episode target; caps episode $2, day $6, story $20 |
| 2 | Keys | fal + paid Gemini; no OpenAI |
| 3 | Video | fal/seedance-1-pro-fast 720p on every shot; 1080p a per-story switch |
| 4 | Writing LLM | Nvidia free endpoint if possible, else OpenRouter |
| 5 | Shots | 6–10 shots of 5–12 s, one clip each, window 55–75 s kept |
| 6 | Existing stories | Untouched; deepen/regenerate opt-in later |
| 7 | Structure | Narrator on; judge may block approval; two_line subtitles (150 ms floor on word_pop); hard length gate |
| 8 | Knowledge base | Full, dashboard-approved before ep 1 |
| 9 | Free route | Drafts and style previews only |
| 10 | Defaults | Tier 2 + quality preset when keys present; allow_paid stays off |
| 11 | TTS | Edge; rate/pitch per character from the voice spec and the line's delivery |

**Three choices in this plan need the human's go with the approval** (each has my recommendation):
1. **The hook shot may run 3–6 s** (the one exception to 5–12 s; a 5 s hook loses the scroll-stopper). Recommended.
2. **Keyframes on `fal/seedream-4.5-edit`** ($0.04, seeds honoured, 10 references) with `gemini/nano-banana-2-lite`
   as the stop-and-ask fallback; the difference is ≈ $0.05 per episode. Recommended.
3. **The episode lands near $1.73, not $1.50**: a 55–75 s window cannot bill fewer than ≈ 56 seedance seconds ($1.23)
   plus ≈ 0.5 s rounding per shot and $0.32 of keyframes. Under the $2 cap with ≈ $0.27 headroom. Recommended as is.

## A. Design decisions (mine, one line of rationale each)

- **A1 One switch.** New optional `story.generation_profile.pipeline: "v2"`, written on every story created from
  stage 2 on; absent = today's behaviour. It gates layered prompts, looks, quality-only links, the knowledge gate, the
  judges, the length gate, prosody and the v2 typography keys. RC-M3 holds by construction, no flag sprawl.
- **A2 Image link per role** (new pure `clipping/aistory/media_policy.py`, `role_chain(role, kind, merged, story)`,
  plugged into `imaging.resolve` `imaging.py:91` and the chain sites of `steps/assets.py`): sheets, plates and props on
  `gemini/nano-banana-2` ($0.067; 4 character + 3 style references, native 9:16 1K); keyframes on
  `fal/seedream-4.5-edit` ($0.04) then `gemini/nano-banana-2-lite` ($0.0336); video `fal/seedance-1-pro-fast` 720p.
- **A3 Quality-only rule (v2).** `media_policy.LOW_QUALITY_LINKS` = cloudflare/flux-1-schnell, pollinations/flux,
  fal/flux-schnell, openai/gpt-image-2-low: never in a sheet/plate/prop/keyframe chain; still used by
  `steps/style_preview.py`, labelled draft. With no quality link runnable the step stops before any call with a
  DEC-117-shaped message (why, the hardware advice, the preset estimate, the keys to add); the prompt-only offer is
  hidden for v2 stories.
- **A4 Budget-profile `images` policy made real** (E5 D4): `budget.profile_settings()` read by `media_policy`; new policy
  `quality_roles` with a `roles` table; the unused `quality` profile becomes "Quality (billed APIs)": cap 2.0,
  `images quality_roles`, `animate all_shots`, `video_link_policy first_in_chain`, `video_resolution 720p`.
- **A5 Nano-banana on the paid key.** `LINK_ENV_KEYS` gains both nano-banana links on `GEMINI_PAID_API_KEY`;
  `GeminiImageAdapter.generate` (`images.py:185`) reads `env_keys_for(link)[0]` and sends `imageConfig.imageSize "1K"`.
  Amends DEC-205 / RC-V4 (closes the phase-6 follow-up).
- **A6 Keyframe 9:16 at the source** on v2: centre-crop to an exact 9:16 multiple before storing, with DEC-217's rule and
  `render/imagesize.py`; closes the DEC-216 source-crop follow-up and the near-9:16 concat edge for new stories.
- **A7 Negative prompts.** Kept computed and hashed as today (no hash moves; kling still sends it). Links with no
  negative field get a short positive constraints clause instead: images ≤ 12 words ("Clean frame: no captions, logos or
  watermarks; each character appears once."), clips ≤ 15 words ("The set, the lighting and every character's look stay
  exactly as in the first frame."). A 40-word avoid-list wastes the window and names what it forbids.
- **A8 Layered prompts**, pure builders in `prompting.py`, rendered at resolve time and stored so the shot card shows what
  is sent: sheet ≤ 130 w (full-body reference from the look, items "carrying/with", plain background); plate ≤ 150 w
  (descriptor, layout map, lighting per variant, props that live there, no people); prop ≤ 80 w (look, material, real
  scale); keyframe 130–220 w in this order: reference roles → beat (T1 v2 action ≤ 45 w + the lines' delivery/emotion)
  → staging (position, facing, expression per subject + one relative-height sentence) → composition coerced to the
  subject count → place slice (full layout for wide/medium, lighting + one background element for close/insert) →
  style tail (rendering + palette) → constraints; clip ≤ 80 w (subject handle + T1 v2 `motion`, secondary motion,
  camera, stays-still clause, `tier2_prompt_suffix`), stored in the existing always-None `shot.video_prompt`
  (`shots.py:934`).
- **A9 Reference roles in text.** `_reference_images_v2` orders: one identity sheet per character (full-body portrait,
  or the expressions sheet for close-ups), the plate variant, each turnaround, props; cap 10 (`maxItems` 8 → 10);
  `role_text` writes "Image 1 is … (identity: keep exactly); image 3 is the set …". `request_parts` slices by
  `REFERENCE_LIMITS {seedream-4.5-edit: 10, nano-banana-2*: 14}`; shots without `prompt_layout` keep `[:4]`.
- **A10 Structured look** as optional blocks via `schemas._document(optional=)`: `character.look` {build, silhouette,
  face, hair, skin_material, height_cm, palette ≤ 4, wardrobe_sets 1–3 {id, context, items}, season_change};
  `character.dossier` {backstory ≤ 60 w, goal, need, fears, secrets ≤ 2, relationships [{with, history, now}],
  voice {patterns, vocabulary, catchphrases}, arc}; `place.look` {layout_map {left,right,back,foreground,centre},
  scale_note, lighting {variant: text}, props_here}; `prop.look` {scale_cm, material, colour, scale_phrase,
  where_when [{ep, holder, place, note}]}. Renderers `shots.render_look/render_place/render_prop` (≤ 45 w, relative
  height from `height_cm` against the shot's other subjects; items de-duplicated against wardrobe).
- **A11 Props as entities.** The knowledge step registers every object the timeline names before ep 1; from ep 2 E1
  gains optional `new_objects ≤ 2` replacing `_E1_NO_PROPS_LINE` (`prompts.py:827`, amends DEC-171); the script step
  creates stubs, R1 v2 writes the look, the storyboard refuses while a scene's prop has no approved image (estimate
  shown); `insert_prop` without a prop is refused by `validate_t1` on v2.
- **A12 Shot structure.** New template `templates/episodes/serial_60s_v2.json`: window [55, 75], target 62, scenes and
  shots [6, 10], body slots [5.0, 11.0] s, hook [3, 6] s (the one exception), cliffhanger [4, 10] s, optional
  `shots_per_scene [1, 2]`, `max_shot_s 12`, `hold_extension_max_s 1.0`. `EpisodeContext.episode_defaults` merges the
  template's pair (`episode_common.py:127`); `build_storyboard` takes the effective pair instead of `shots.py:962`.
  T1 v2 asks 1 shot per scene (2 only above `max_shot_s`); a shot covers its scene's consecutive lines (1–3 lines at
  ≤ 11 s). DEC-208 unchanged: seedance gets `ceil(duration)` (≈ 0.5 s waste per shot, ≈ $0.09 per episode). v1
  templates, files and tests untouched.
- **A13 Context builder.** `context.slice_for_scene` (goal, need, relevant secret, catchphrases, relationship history
  among those present, knows-so-far from the timeline, ledger state, beat purpose, place layout + lighting) and
  `context.slice_for_shot` (subjects with look/wardrobe/position, props with holder, place slice, lines with
  delivery, previous shot's action and staging); word-capped; separate `INPUT_BUDGET` keys E1v2 2400, E2v2 2200,
  E3v2 2900, T1v2 2000 (v1 budgets untouched, RC-M1).
- **A14 Knowledge-base placement**: cast step v2 = K1 → D1 dossier → D2 look → sheets (the look exists before the
  portrait; character approval covers both); places step v2 = P1 → D3 place look → plate; new `knowledge` step
  (`steps/knowledge.py`, resumable, saved after every call): D4 world geography/period/motifs, D5 timeline one call
  per planned episode (`beats ≤ 8 {what, place, who, objects, knows_after}`), D6 props registry ≤ 8 (creates prop
  entities; R1 v2 + prop images, estimated), the ep00 ledger seed. Stored in `knowledge.json` (`story_knowledge_v1`,
  `approved_at`, `rev`). Gate: `check_episode_preconditions` refuses a v2 script until the knowledge is approved and
  current (the DEC-130 pattern; no seventh approval key, no status change).
- **A15 Writing chain.** Optional setting `STORY_LLM_CHAIN` read by `steps/llm_call.resolve_chain` (`:95`) before
  `LLM_CHAIN`; `registry.DEFAULT_STORY_LLM_CHAIN` = `nvidia/<benched strong model>` → `openrouter/mistralai/
  mistral-medium-3.1` ($0.40/$2.00 per M, read live 2026-10-01; EU host $0.44/$2.20; structured outputs) → today's
  free links. DEC-115 skips the paid link without `allow_paid`; DEC-206 books it; `LLM_PRICES` gains the row. The NIM
  model is picked by a free `tools/bench_llm.py` run on real K1/E2 FR requests (candidates: DeepSeek V3.x, Qwen3
  235B, Mistral Medium/Large, Kimi K2); its family joins `_NIM_REASONING_FAMILIES` (`llm.py:186`) if it thinks.
  NIM latency (~85 s per 400-token reply) is absorbed by one artifact per call ≤ ~420 tokens, a resumable knowledge
  step, DEC-131's predictive budget fed the measured latency, and a 330 s timeout falling through.
- **A16 Judges.** J1 script (text, writing chain, analytic temperature) after E4: returns who-wants-what /
  what-happens / why-it-matters, `passed`, issues {unclear_goal, unmotivated, unintroduced, object_unseen,
  repeated_line, no_hook_text}; a deterministic duplicate-line check (normalised-token Jaccard ≥ 0.7) merged in;
  `approve_script` (`workflow.py:2852`) refuses unless "approve anyway". J2 keyframes (vision, existing `VISION_CHAIN`
  on the Gemini free tier, the `uploads.describe_upload` pattern `uploads.py:554`): one call per shot with the keyframe,
  the previous keyframe and the beat/staging/props text → {shows_beat, missing, continuity_issue}; it blocks a new
  keyframe approval (`assets.json.keyframes_approved {at, anyway, fingerprint}`) and v2 clips are never bought
  before that approval is current. Vision, not a text proxy, because E4's top causes are off-model frames and the
  missing object.
- **A17 Length gate, narrator, subtitles, TTS.** v2: `approve_script`/`approve_storyboard` refuse outside the window
  (no "anyway"); the script step runs a bounded fill pass (E2 again on the 2 shortest body scenes, ≤ 2 calls);
  `approve_assets` and the render refuse on measured durations outside the window. v1 keeps the warning. Narrator on
  for new stories (`store.py:718`) with an auto-pinned locale voice distinct from the cast; voice proposals filtered
  to the story's locale (fixes the fr-CA mayor). New v2 style locks write `typography.subtitle_mode two_line` and
  optional `word_min_card_ms 150`; `render/subtitles.py` applies the floor only when the key is present (RC-M2 golden
  unedited). `voices.prosody_for(voice, line)`: base rate/pitch from the dossier's voice at pin time, a per-emotion
  delta clamped to ±20 % / ±8 Hz; v2 only.
- **A18 Hardware advice.** `hardware.RECOMMENDATIONS` for cpu_only / container_no_gpu / low: "No good local image or
  video model on this host. Recommended: the Quality (billed APIs) preset, ≈ $1.70 an episode (8 shots animated) plus
  ≈ $1.00 once per story for sheets, plates and props. Add FAL_KEY and GEMINI_PAID_API_KEY." Numbers from a pure
  `media_policy.preset_estimate()` so they cannot drift from `pricing.py`.
- **A19 Dashboard.** New `KnowledgeStep.jsx` after Season (dossiers, looks, timeline, props with images, ledger seed,
  approve); `CastStep.jsx` dossier + look fields; `PlacesStep.jsx` layout map + lighting; `NewStoryWizard.jsx` preset
  select defaulting to Quality when both keys are set, with the estimates; `StoryboardPane.jsx` shot card: reference
  roles, clip prompt, word counts vs model targets, J2 verdicts, "Approve keyframes / anyway"; `Settings.jsx` the
  hardware advice card. No auth anywhere.
- **A20 Caps** 2.00 / 6.00 / 20.00 (`budget.py:31-33`) in all five places; `allow_paid` stays off.

## B. Stages (each committable and revertible alone; commits by explicit paths; no trailers)

Schema rule: the commit that adds optional schema keys is its own commit; revert a stage's behaviour commits, never its
schema commit once a v2 document exists. Each stage: Tier-1 (`-n 4`, both environments, by the orchestrator, no scratch
server running) → commit → action-log line.

| Stage | Goal | Files | Risk | Tests (fail-first) | RC at risk | Agent | DEC |
|---|---|---|---|---|---|---|---|
| **1 W0** | D1 `video_action` stored by `resolve_shot`/`build_storyboard`/`refresh_prompts`, read by `build_video_prompt` (`video_plan.py:145`); D2 `without_names` on the resolved action only (`shots.py:373` sweep dropped); D3 `_strip_trailing_period` in `portrait/turnaround/expressions_prompt` (`prompting.py:172/184/196`), the plate run-ons (`:206`, period after `environment_rules` `:207`) and `character_prompt_block` (`:244`) | `shots.py`, `video_plan.py`, `schemas.py` (shot optional `video_action`), `prompting.py` | low | `test_story_video_plan::test_clip_prompt_has_no_entity_tags`; `test_story_shots::test_place_name_in_its_own_descriptor_survives`; `test_aistory_prompting::test_sheet_and_plate_prompts_have_no_period_comma` (+ `character_prompt_block` added to the guard `:356-367`). No golden re-pin. Stored clips stay `current` (E5) | M3 (none by construction) | Sonnet | 220 |
| **2a Quality chains, preset, caps, sticky references** | A2–A6, A20; v2 stories default tier 2 / api / references / `quality`; prompt-only offer hidden on v2; seedream-4.5-edit link, inputs and price; nano-banana on the paid key + `imageSize 1K`; v2 keyframe 9:16 crop. Agent first reads fal's v4.5/edit OpenAPI schema (free) for size/seed/prompt fields (A-111) and the 8 prompt-only stories' logs (A-123) | new `media_policy.py`; `imaging.py:91`; `refimages.py:419`; `steps/assets.py` chain sites; `providers/images.py`, `pricing.py`, `generation.py` (DEFAULT_CHAINS[IMAGE_EDIT], LINK_ENV_KEYS), `budget.py` (caps, `roles`), `templates/budget_profiles.json`, `defaults.py`, `store.py:695-720`, `clipping/config.py` (five places), `steps/cast.py`/`workflow.py` | med | `test_story_media_policy::test_v2_roles_never_use_low_quality_links`, `::test_legacy_story_keeps_env_chain`; `test_story_images::test_nano_banana_reads_paid_key_only` (RC-V4 re-pinned on purpose), `::test_seedream45_inputs_carry_refs_and_size`; `test_budget::test_cap_defaults_2_6_20` (five-place agreement re-pinned); `test_story_defaults::test_new_story_is_v2_quality_when_keys_present` | V4 (amended), V5, V6, M3 | Sonnet | 221, 222, 223 |
| **2b Writing chain** | A15; the free NIM bench run first (result → A-114) | `steps/llm_call.py:95-108`, `providers/registry.py`, `llm.py:186` (only if a family is added; else `git diff -- llm.py` stays empty, RC-S4), `pricing.py` LLM row, `config.py` | low | `test_story_llm_chain::test_story_chain_prefers_nvidia_then_openrouter_then_free`, `::test_paid_openrouter_skipped_without_allow_paid`, `::test_mistral_medium_priced` | S4 | Sonnet | 224 |
| **3a Structured look + sheet/plate/prop prompts** | A10; D2/D3/R1 v2 builders (caps 380/300/220); v2 call order in cast/places; `render_look/place/prop`; `*_prompt_v2`; turnaround/expressions as edits of the portrait with role text | `schemas.py` (optional `look`, `dossier`), `prompts.py`, `steps/cast.py`, `steps/places.py`, `shots.py`, `prompting.py`, `refimages.py` | med | `test_story_look::test_v1_documents_still_validate`, `::test_render_look_relative_height`; `test_aistory_prompting::test_portrait_v2_full_body_from_look`; legacy strings untouched | M3 | Opus | 226 (1) |
| **3b Layered keyframe/clip prompts, roles, preview** | A7–A9; `prompt_layout "layered_v1"`, `plan` on the shot; `REFERENCE_LIMITS`; `video_plan` reads `video_prompt`; shot-card preview | `prompting.py`, `shots.py`, `schemas.py` (optional `prompt_layout`, `plan`; `maxItems` 10), `steps/assets.py:613-632`, `video_plan.py:144`, `StoryboardPane.jsx` | med | `test_story_shots::test_layered_prompt_beat_before_place_and_roles_first`, `::test_close_up_omits_layout`, `::test_legacy_story_resolves_byte_identical` (A sh01's E1 dump pinned); `test_story_video_plan::test_layered_clip_prompt_under_80_words` | M3 | Opus | 225 |
| **3c Props as entities** | A11 | `prompts.py` (E1 v2 `new_objects` `:826-827/:1016`; `validate_t1`), `schemas.py`, `steps/script.py`, `steps/storyboard.py` | med | `test_story_prompts_episode::test_e1_v2_offers_new_objects_not_always_empty` (v1 string unchanged); `test_story_storyboard_props::test_storyboard_waits_for_new_prop_image` | — | Sonnet | 226 (2), amends 171 |
| **4 Shot structure, seedance on every shot, 1080p** | A12; `all_shots` refuses the whole plan with the numbers when over the cap; seedance resolution from `request.extra`; cache payload adds `resolution` only when ≠ 720p (existing keys unchanged) | new `templates/episodes/serial_60s_v2.json`, `defaults.py`, `schemas.py`, `episode_common.py:127`, `shots.py:962`, `prompts.py` (T1 v2: 1–2 shots, action ≤ 45 w, `motion` ≤ 25 w, `staging`), `steps/storyboard.py:131-170`, `steps/clips.py:370-520`, `providers/video.py:167`, `pricing.py`, `gencache.py` | **high** | `test_story_timing::test_v2_scene_plus_hold_never_exceeds_12s`, `::test_v1_template_unchanged`; `test_story_prompts_episode::test_t1_v2_asks_motion_and_staging` (v1 T1 golden unedited); `test_story_video_plan::test_all_shots_refused_whole_with_numbers`; `test_story_video_cache::test_720p_key_unchanged_1080p_differs` | V6, V7, M2 (guarded), M3 | Opus | 227, 233 |
| **W-mid paid walk ≤ $1.60** | After stage 4, after a shown estimate and the human's go: one new v2 story (2 characters, 1 place, 1 prop, no knowledge base): sheets 6 × 0.067 = 0.40, plate 0.067, prop 0.067, 8 keyframes × 0.04 = 0.32, 4 seedance clips ≈ 30 s × 0.022 = 0.66 → ≈ $1.52. Proves the chains, the paid key, layered prompts and 9:16 before the knowledge base lands. Single CLI processes with per-process caps (DEC-215) | — | — | — | — | orchestrator | — |
| **5a KB schemas + dossier call** | `KNOWLEDGE_SCHEMA` (`story_knowledge_v1`), memory entry optional `ledger`, D1 (cap 420) after K1 on v2 | `schemas.py`, `store.py` (`KNOWLEDGE_DOC`), `prompts.py`, `steps/cast.py` | med | `test_story_knowledge_schema::test_legacy_docs_validate_and_v2_blocks_checked` | M3 | Opus | 228 |
| **5b Knowledge step + gate** | A14: D4 (300), D5 (420 per episode), D6 (450), R1 v2 per prop, prop images, ledger seed; `approve_knowledge`; routes; CLI `step ID knowledge` / `approve ID knowledge`; the precondition gate | new `steps/knowledge.py`, `steps/__init__.py:107`, `prompts.py`, `workflow.py`, `web/api/routes/stories.py`, `cli.py`, `episode_common.py` | **high** | `test_story_knowledge_step::test_v2_episode_script_refused_until_knowledge_approved`, `::test_legacy_story_not_gated`, `::test_step_resumes_after_each_call` | M9 (no auth), M3 | Opus | 228 |
| **5c Context builder wired** | A13; the no-repeat instruction in E2/E3 incl. the cliffhanger block `:1411-1422`; resolvers read the ledger's wardrobe set and holders | `context.py`, `prompts.py`, `steps/script.py`, `steps/storyboard.py`, `shots.py` | **high** | `test_story_context_slices::test_slice_holds_only_present_entities_within_budget`; `test_story_episode_prompt_budgets` gains v2 rows (v1 rows unedited; DEC-138 method) | M1 | Opus | 228 |
| **5d Continuity ledger** | L1 (cap 400) after S3 on v2; `series_memory.fold_ledger(entries, before=ep)` seeded from ep00 | `prompts.py`, `steps/memory.py`, `series_memory.py` | med | `test_story_series_memory::test_ledger_folds_latest_state_per_character` | — | Sonnet | 229 |
| **6a J1, duplicate check, hook text, length gate** | A16 J1, A17 gates; `validate_e3` hook text required on v2 (`:1550-1559`); `validate_e2` duplicate check; the fill pass; v2 render refuses outside the window | new `steps/judge.py`, `prompts.py`, `steps/script.py`, `schemas.py` (script optional `first_watch`), `workflow.py:2852/:2891/approve_assets`, `steps/render.py` | med | `test_story_judge::test_failed_first_watch_blocks_approval_unless_anyway`; `test_story_length_gate::test_v2_render_refused_outside_window_v1_warns` (story B's shape); `test_story_prompts_episode::test_e2_rejects_near_duplicate_line` | M3 | Opus | 230, 231 |
| **6b J2 keyframe judge + approval** | One vision call per shot (≤ 160 tokens out); `keyframes_approved`; v2 clips refused before approval | `steps/judge.py`, `schemas.py`, `workflow.py`, `steps/clips.py`, routes, CLI | med | `test_story_keyframe_gate::test_no_clip_bought_before_keyframes_approved_v2` | V7 | Opus | 230 |
| **6c Narrator, subtitles, prosody** | A17 rest; locale filter | `store.py:718`, `steps/cast.py`, `voices.py:684`, `stylelock.py`, `schemas.py`, `render/subtitles.py` | low–med | `test_aistory_render_subtitles::test_word_pop_floor_only_when_lock_says`; `test_story_voices::test_v2_line_prosody_legacy_request_identical`. RC-M2 golden unedited (no key in `golden.py:102`) | M2, M3 | Sonnet | 231 |
| **7 Hardware advice + dashboard** | A18, A19 | `hardware.py:220-262`, `media_policy.preset_estimate`, new `KnowledgeStep.jsx`, `CastStep.jsx`, `PlacesStep.jsx`, `NewStoryWizard.jsx`, `StoryboardPane.jsx`, `Settings.jsx` | low | `test_hardware::test_weak_hosts_recommend_billed_preset_with_cost_and_keys`; dashboard build; Tier-2 at 375 px | M9 | Sonnet (Haiku for the text) | 232 |
| **8 Docs, close, acceptance walk** | `docs/AI_STORY.md`, MASTER-SPEC deltas (§2.3–2.5 optional blocks, §4.2 rows D1–D6/L1/J1/J2, §6.2 v2, §8.5), DECISIONS, ASSUMPTIONS, CHECKPOINT; docs only → no Tier-1 re-run | docs, `.claude/` | low | — | — | Sonnet | — |

## C. The acceptance walk (one new v2 story, ep 1, on the preset)

Every paid step is a single CLI process in the container after its shown estimate and the human's go, with its own
`ALLOW_PAID` and caps (DEC-215 / the `walk6.py` pattern). Settings' `allow_paid` stays off. Deploy only at 0 jobs.

| # | Step | Expected $ | Process cap |
|---|---|---|---|
| 1 | New story on the Quality preset, concept, bible (LLM on nvidia free, else mistral-medium booked) | ≈ 0–0.01 | story 0.05 |
| 2 | Style; the three-image preview on free links, labelled draft | 0 | — |
| 3 | Cast: K1, D1, D2 per character; sheets on nano-banana-2: 3 × 3 × 0.067 | 0.60 | story 0.75 |
| 4 | Places: P1, D3; 2 plates (+1 night variant) × 0.067 | 0.13–0.20 | story 0.25 |
| 5 | Season; knowledge D4–D6 (text ≤ 0.05 if booked); 3 props × 0.067; the human approves the knowledge base in the dashboard | 0.20–0.25 | story 0.30 |
| 6 | Script ep 1: E1–E4 + J1 (free); approval, the judge may block | 0–0.03 | — |
| 7 | Storyboard: T1 v2 × 8 (free); approval; estimate inside 55–75 s | 0 | — |
| 8 | Assets, animate off: 8 keyframes × 0.04, edge TTS, J2 (free); the human approves the keyframes | 0.32 | episode 0.45 |
| 9 | Assets, animate on: 8 clips, Σ ceil ≈ 64 s × 0.022 | ≈ 1.41 | episode 2.00 |
| 10 | Render (hard length gate), metadata; the human watches on the phone: the story is clear, the looks are good, every shot moves | 0 | — |

Episode ≈ $1.73 (≈ 60 s of animation 1.32 + ceil waste 0.09 + keyframes 0.32), ≈ $0.27 headroom under the $2 cap;
story ≈ $2.7–2.8 under the $6 day cap and $20 story cap.

## D. Riskiest stage: 5 (5b the knowledge step and gate, 5c the context builder)

Four document schemas, a new step with an approval and an episode gate, every writing prompt rewired to slices, run on
a model measured at ~85 s per call (a full base ≈ 40 calls ≈ an hour), and prompt budgets that can squeeze out the beat.
Mitigations: four revertible sub-stages with the schema commit separate; one artifact per call ≤ ~420 tokens and a
resumable step; v2 `INPUT_BUDGET` keys measured by the DEC-138 method with a fail-first budget test; slices hold only the
present entities; v1 prompt goldens untouched; W-mid runs before stage 5 so chain or image problems are not confused
with knowledge-base problems. Second risk: stage 4 (timing is the render's byte-identical path): a new template file
only, v1 tests left as guards.

## E. Rejected alternatives

1. Keep 18–20 shots and animate them all: $1.58 of seconds for A plus ≈ $0.80 of keyframes; 2–3 s clips too short to act a
   beat; worse ceil waste. Rejected for 6–10 beat clips (the human's choice).
2. A text-only judge: E4's main causes (an off-model frame, a missing object) are invisible to a script reading.
   Rejected for J2 on the free vision chain; J1 stays text.
3. A separate dossier document per entity: duplicates the entity lifecycle (approval, staleness via `resolved_from`,
   media). Optional blocks on `character.json`/`place.json` reuse `outdated_entities` (`workflow.py:2094`) and
   `refresh_prompts`; only story-level knowledge goes in `knowledge.json`.
4. Veo-lite for native audio: $4.00–4.80 per episode, against DEC-201, 4/6/8 s lengths. Rejected.
5. One giant prompt per call type: that is what E1 measured (70 % boilerplate, the action at 7.6 %). Rejected.
6. A seventh story approval key: changes the closed approvals schema and `derive_status` for every stored story
   (`store.py:260-345`). Rejected for the DEC-130-style episode gate.

## F. DECISIONS check

DEC-219 implemented (stages 2–6). DEC-194/215 paid runs kept, checked. DEC-204 sticky links kept (the preset's role
chain is what the sticky rule picks from; the keyframe fallback is a stop-and-ask), checked. DEC-205 amended by DEC-222
(nano-banana on the paid key; RC-V4 re-pinned). DEC-206 used for OpenRouter (price row added; DEC-224). DEC-115
unchanged, checked. DEC-216/217 render rules unaffected; A6 closes their follow-ups for new stories, checked. DEC-117
prompt-only stays a labelled choice for v1, hidden for v2 (DEC-221). DEC-130/179 memory gate extended by the knowledge
gate (DEC-228). DEC-177/178 memory stays a fold; the ledger folds the same way (DEC-229). DEC-168 pacing unchanged,
checked. DEC-202 video last; the keyframe approval sits between two assets runs (DEC-230). DEC-203 plan stays derived;
`all_shots` refuses whole over the cap (DEC-227). DEC-208 kept; v2 makes a shot ≤ 12 s at the source. DEC-171 amended by
DEC-226 (E1 `new_objects`). DEC-027 / spec §4.1 pack budget amended by DEC-228 for v2 prompt ids only. DEC-142/183
timing unchanged (v2 is template data). DEC-129/185 approvals stay on the episode; the gates add refusals (DEC-231).
DEC-173 / RC-M9 no auth, checked. DEC-176 / DEC-192 tests, followed.

## G. Assumptions to register (A-110+)

A-110 seedance prompt limit unknown (keep ≤ 80 w). A-111 seedream-4.5-edit size floor, seed, negative and prompt
fields (fal schema read in stage 2a). A-112 nano-banana has no negative and no stated limit; positive phrasing honoured.
A-113 the paid Gemini project has the image models enabled. A-114 the benched NIM model writes valid FR JSON in ≤ 330 s.
A-115 mistral-medium-3.1 price as read 2026-10-01. A-116 a 150 ms word_pop floor is legible. A-117 J2 recall/false
positives unknown ("anyway" stays). A-118 seedance keeps identity with the stays-still clause over 5–12 s. A-119 episode
cost ≈ $1.70. A-120 6–10 beat clips read better than 18–20 cuts (the walk confirms). A-121 Gemini 9:16 1K ≈ 768×1376.
A-122 v2 inputs fit ≤ 2,900 tokens. A-123 8 of 9 stories are prompt-only because the edit chain never runs while
`allow_paid` is off (logs to confirm). A-124 the hook exception (≥ 3 s) is acceptable to the human.

## H. Verification

- **Tier-1** after every code stage: local `PYTHONPATH=~/.cache/rzc-xdist python -m pytest -p no:warnings -n 4` and the
  CI env `PYTHONNOUSERSITE=1 PYTHONPATH=/tmp/cilibs:~/.cache/rzc-xdist python3 -m pytest -p no:warnings -n 4`, plus
  `compileall`; no scratch API server running. The RC-M2 tier-1 golden stays unedited through the phase.
- **Tier-3**: the tests named per stage (one fail-first per fix); two re-pins on purpose in 2a (RC-V4, the five-place
  caps agreement); the v1 goldens (prompts, T1, storyboard, render) untouched.
- **Tier-2 (live)**: W-mid after stage 4 (≤ $1.60); the dashboard at 375 px after stage 7; the acceptance walk (C).
  Each paid run after a shown estimate and the human's go. Deploy at 0 jobs by fast-forward of `main`; Python changes
  need a container restart, dashboard changes the `rm -sfv` rebuild.
- **Regression contract carried**: RC-V1…V8, RC-M2, RC-M3 (stored stories byte-identical: proven by re-rendering one
  stored episode at $0 after stages 4 and 6c), RC-M8, RC-M9 (no auth), RC-A1 (nothing under `clipping/studio/**`).

## On approval (protocol steps before any code)

1. `git worktree add .claude/worktrees/ai-story-phase-7 -b feat/ai-story-phase-7 main` (30604dd); never touch the main
   checkout's branch.
2. CHECKPOINT.md: in-progress header (phase IMPLEMENT, stage 1, next action), the checkpoint hash, the regression
   contract with test mapping, the plan path; ASSUMPTIONS A-110…A-124 UNCONFIRMED; DECISIONS DEC-220 reserved; action-log
   line for EXPLORE/CLARIFY/PLAN. Commit by explicit paths.
3. Tier-1 baseline in both environments (expected ≈ local 6635/1 skipped, CI env 5841/761 as at the phase-6 close);
   record in CHECKPOINT.
4. Stage 1 (W0) by a Sonnet agent on the exact files above; fail-first proven on the three tests before the fix.

---

# Appendix — EXPLORE record (2026-10-01, read-only, no paid call)

| Id | Agent | Model | Output |
|---|---|---|---|
| E1 context anatomy | Explore | Opus | `~/.claude/plans/pasted-content-id-b880-start-ai-luminous-bubble-agent-abe18b955ad1917c9.md`; scratchpad `e1/dumps/` (165 files), `e1/side-by-side.html` (sent to the human) |
| E2 live model guides + prices | Explore | Sonnet | below |
| E3 knowledge-base gaps | Explore | Sonnet | below |
| E4 comprehension diagnosis | Explore | Sonnet | below; contact sheets in scratchpad `e4/` |
| E5 W0 defect paths + fixes | Explore | Sonnet | below; scratch copies `e5/A`, `e5/B` |
| Nvidia link check | Explore | Sonnet | below |
| Plan design | Plan | Opus | sections A–H above |

### E2 — model guides and live prices (read 2026-10-01)

Pages read live: fal model pages (Seedream 4.5 t2i and edit, Seedream 4 edit, FLUX.2, FLUX Kontext pro, flux/schnell,
Seedance 1 pro-fast, Seedance 2.5, Kling 2.5 turbo std/pro, Kling 3 turbo, O1, O3, LTX-2.3 fast, flux-2 page);
ai.google.dev pricing, image-generation and veo docs; developers.openai.com pricing and image guide
(platform.openai.com redirects there); openrouter mistral-medium-3.1 endpoints. UNREACHABLE: Artificial Analysis arenas,
build.nvidia.com (JS-only). Third-party only: Seedream 4.5/5 encoder limit, gpt-image per-image prices, Wan 2.6,
Hailuo 2.3, Kling 3 standard price.

Price drift against `pricing.py` (2026-09-25): none for seedance-1-pro-fast (0.022/s 720p, 0.0486/s 1080p),
kling-2.5-turbo-std (0.21 per 5 s + 0.042/s), veo-3.1-lite (0.05/0.08), nano-banana-2 (0.067), nano-banana-2-lite
(0.0336), seedream-4-edit (0.03). LTX-2.3 fast shows both 0.06 and 0.04. **Imagen 4 retired from the Gemini API
(2026-08-17).** FLUX Kontext's successor is FLUX.2 (dev 0.012/MP, pro 0.03, flex 0.05, max 0.07; up to 10 refs by
`@image1` syntax). Seedream 4.5 edit: $0.04, up to 10 references (the last 10 used), output ≤ 4 MP, references as
"composition/element sources" by natural-language instruction, seed/negative/safety not shown. Kling: 2.5 turbo pro
$0.35 per 5 s + $0.07/s; v3 turbo pro $0.14/s 1080p; v3 turbo std ≈ $0.112/s; O3 std $0.084/s (audio off) / $0.112/s
(audio on), 3–15 s, start + end frame, native audio; O1 ≈ $0.112/s first + last frame. OpenAI: token rates only
(gpt-image-1 $10/$2.50/$40 per M; -mini $2.50/$0.25/$8; gpt-image-2 $8/$2/$30); forum token counts give -mini high
1024×1536 ≈ $0.050, gpt-image-1 high ≈ $0.25 (UNCONFIRMED). Gemini image: "up to 14 images … up to 4 images of
characters … up to 3 images … as style references"; ratios incl. 9:16; sizes 0.5K/1K/2K/4K; no negative prompt; no
stated length limit. Veo 3.1: prompt fields subject/context, action, camera & composition, style, audio cues; up to 3
reference images; last frame by interpolation; `negativePrompt` absent on the Gemini API page (Vertex only); prices
veo-3.1 $0.40/s, fast $0.10/$0.12, lite $0.05/$0.08 (1080p forces 8 s).

Every-shot episode cost at today's 18–20 shots (DEC-208 rule; A Σ 59.87 s, B Σ 48.34 s): seedance-1-pro-fast A $1.58
(720p) / $3.50 (1080p), B $1.32 / $2.92; kling-2.5-turbo-std A $4.83, B $4.20; veo-3.1-lite A $4.80 / $12.80, B $4.00
/ $11.52; ltx-2.3-fast A $7.56, B $6.48; seedance-2.5 (audio, 720p only, 0.473/s) A $43. Episode images at 27 (A) /
25 (B): seedream-4-edit 0.81/0.75; nano-banana-2-lite 0.91/0.84; nano-banana-2 1.81/1.68; gpt-image-1 high 6.75/6.25.

### E1 — context anatomy (dumps verified byte-for-byte against the stored prompt hashes of all 38 shots and both clips)

Numbers: shot image prompt A 187–257 w (mean 224), B 199–283 (mean 242); sheets 84–120 w; plate 104/120 w; clip prompt
24/28 w (+ a 40-w negative). Share of a shot prompt: style block 37 % and setting 31–34 % (identical in every shot),
subjects 16–19 %, camera 5–6 %, **action 7.6–7.8 %** after 34–72 words of descriptors; identical-for-the-episode share
68–71 %. CLIP-77 ends before the action in every two-character shot; past an estimated T5-256 window "Vertical 9:16" is
cut in 26/38 shots (limits UNVERIFIED). LLM user prompts: E1 ≈ 610 t (cap 1450 out), E2 ≈ 720–810 (600), E3 ≈ 950 (720),
E4 ≈ 1000 (800), T1 ≈ 580–630 (580), K1 ≈ 580 (750), P1 ≈ 590 (260); R1 never called.

Findings (file:line at main): (1) `cloudflare/flux-1-schnell` sends only `{"prompt","steps":4}` (`images.py:137-140`):
no size, seed or negative; 13/20 A and 18/18 B shots are 1024×1024; B's kling clip became 960×960. (2) No hosted image
adapter sends the negative (`images.py:129-374`); only kling among video links (`video.py:177`). (3) pollinations/flux
returned off-style watermarked humans for all 4 portraits, A's plate and 7 A shots. (4) ≈ 70 % boilerplate per shot
prompt incl. 69/81 words of place layout on close-ups (`prompting.py:153-160`, `shots.py:255-258`). (5) Clip prompt =
raw T1 action (`clips.py:220-221` → `video_plan.py:145`); `shot.video_prompt` always None (`shots.py:934`). (6) Plot
objects are not entities (`prompts.py:826-827, 1006` "props: always []"); A's Mittens not in the cast; B `insert_prop`
3× with no prop. (7) No staging between subjects (`shots.py:261-289`); framing contradicts subjects (B sh09/sh10);
`lens_phrase` forces 85 mm shallow focus on a flat triangle (`prompting.py:104-105`); flux-kontext-pro keeps only ref 1
(`refimages.py:112`). (8) Script intent never reaches the generators; T1 never sees `place.descriptor`, delivery or
the previous shots' actions (`storyboard.py:137-169`). (9) Turnaround/expressions never consumed (`shots.py:305`); the
plate omits `layout_notes` (`prompting.py:204-212`); run-ons "mouth., wearing" in `portrait_prompt`/sheets
(`:172/:184/:196`), "bench., day" (`:206`), no period after `environment_rules` (`:207`); "wearing <tool>" and
duplicated items. (10) TTS gets text + voice id only; rate/pitch null (`voices.py:684-689`, `tts.py:191-211`); B's mayor
is fr-CA. (11) B sh01 1.0 s bought as 5 s of kling ($0.21). (12) One seed per lead in prompt-only
(`assets.py:378-386`). Corrections to the brief: prompts run 187–283 w; `character_prompt_block`'s `prompt_block` is
never sent (the sent defect is in the sheet builders); `without_names` is `names.py:18-37`; shot prompts do include
layout notes.

### E3 — knowledge-base gaps

Stored today (A): descriptor 25 w (cap 45), 2 items, traits 4, wants 17 w, fears 13 w, speech_style 15 w,
relationships 1 entry, voice {edge, id, direction, sample_line}, `state` always `{alive, location null, arc_notes []}`,
refs all three; place descriptor 27 w, layout 40 w, only `day`; season arc ep1 44 w + 1 hook; `series_memory` empty;
world 43 w + 5 rules + period + 3 motifs; themes 3. Never filled: props, state, series memory, other variants,
`refs.extra/uploads`, feedback. Readers: `context.entity_lines` (`context.py:118-145`: name, role, one_line, ≤ 12-word
descriptor clip, items → K1/P0/P1/R1/S1/S2), `_personality_block` (`prompts.py:728-738`: wants, fears, speech_style →
E2/E3), `_e4_cast_block` (speech_style), `_id_name_block` (E1: id + name). Sheets read descriptor + items; shot image
reads descriptor + items (`shots.py:243-248`), place descriptor + layout (`:255-258`), attaches only `refs.portrait`
(`:292-328`); plate reads descriptor without layout (`prompting.py:204-212`). TTS reads provider/id/rate/pitch only
(`voices.py:682-689`); `voice.direction` and line `delivery` never in the normal flow (`steps/voice_lines.py:309-330`).
Dead context: `state.*`, `relationships` (only in K1's own regenerate, `steps/cast.py:337-356`), `traits`,
`voice.direction`, `delivery`, `themes_and_values`, `genre_tags`, `audience`, `why_come_back` (not even M1
`prompts.py:2241-2278`), `series_memory.introduced` (`steps/cast.py:260-269`), turnaround/expressions as input.
Needed and stored nowhere: relative scale, per-variant lighting text, props at a place, per-character palette and
wardrobe, continuity state. Series memory: `entries[epNN]` {recap ≤ 40 w, hooks_opened/closed, relationship_deltas}
(`steps/memory.py:90-101`, `schemas.py:1506-1517`) folded by `series_memory.py:129-166` for `context.memory_section`
(`context.py:344-381`). Compatibility: all docs `*_v1`; the mechanism is `schemas._document(optional=)`
(`schemas.py:1083-1094`), used for `ARC_ENTRY_SCHEMA.history` and `series_memory.entries`; CHARACTER/PLACE/PROP have no
optional block yet. Dashboard `CastStep.jsx` edits descriptor (:430), items (:434), personality (:522-544), voice
(:550-557); relationships and state not shown. Fingerprint (`steps/assets.py:438-471`) = (prompt_hash, image sha,
locked) per shot: new fields change nothing until a resolver reads them; edits re-prompt only via
`shots.refresh_prompts` (`shots.py:969-997`).

### E4 — comprehension diagnosis

A (EN, 56.9 s, 10 scenes, 20 shots, 5 without a line): l04 ≈ l08 3 s apart; climax l37 = l40 verbatim at 51.2 and
53.2 s then cut to black; no no-repeat instruction in `build_e2`/`build_e3` (titles have one, `prompts.py:303/318`),
and the cliffhanger prompt shows the previous line verbatim (`:1411-1422`); Mittens never appears; `cliffhanger.reveal`
(`script.json:469`) never shown; the toaster on screen ≈ 6 % of the runtime; ≤ 4–5 of 20 keyframes show their action;
sh01/sh12/sh18 are pixel-identical pollinations images (verified by the orchestrator: same seed 372827330 on 5
Captain-led shots, `assets.py:378-386`; pollinations returns the same picture per seed; not a render bug,
`render_manifest.inputs` is correct); sh01/09/17/19 fell back to pollinations after `cloudflare … 170/170` and
`pollinations HTTP 402`; `word_pop` 144 cards, 45 under 150 ms, 30 under 100 ms; `prompt_only` 20/20. B (FR, 45.1 s, 9
scenes, 18 shots, 6 without a line): 9.9 s under the floor with only a warning; `hook.on_screen_text: null`
(`prompts.py:1332`); mayor unintroduced; the key appears at s08 with no setup; s01/s02 near-duplicate; the mailbox
absent from sh11 when named; the key a speck in sh17; sash and moustache drift; "MAYOR" in English; style consistent.
Ranked causes A: off-model/repeated images + undrawn antagonist, prompt-only, duplicated lines, subtitles, absent
narrator/Mittens/reveal. B: length, no hook text + objects invisible when named, duplicate/causality, continuity drift.

### E5 — W0 defects (reproduced dry on scratch copies)

D1 `clips.py:220` → `video_plan.py:104/144` uses `shot["action"]`; only `resolve_shot` (`shots.py:342-382`) resolves
tags and never stores the result; fix = additive optional `video_action` + `shot.get("video_action") or
shot["action"]`; the video cache key covers `prompt` (`gencache.py:117-150`), `clip_state` (`clips.py:226-240`)
compares `clip_prompt_hash` (`:203-208`); stored storyboards have no `video_action` so A's and B's sh01 clips stay
`current`; the field appears on a new storyboard or `refresh_prompts`. D2 `names.py:18-34` `without_names`, callers
`shots.py:373` (bug), `refimages.py:250`/`assets.py:336` (user note), `assets.py:348` (override); all 20 A shots carry
"A bustling urban the place"; B 0/18 (names never recur in descriptors); fix = strip the resolved action only; no golden
re-pin. D3 `prompting.py:239-245`; callers `steps/cast.py:331`, `refimages.py:584`, `workflow.py:1509`; fix =
`_strip_trailing_period`; add the builder to the double-punctuation guard (`:356-367`). D4 `_REQUIRED_PROFILE_KEYS`
`clipping/providers/budget.py:96`; `profile_settings()` (`:119`) read only at `steps/clips.py:391` for `animate`,
`animate_priority`, `video_link_policy`; use it (W1 hook), don't remove. Coverage: clips → test_story_clip_estimate/
controls, test_story_video_phase, test_story_render_clips, test_story_render_native_audio, test_story_cli_clips;
video_plan → test_story_video_plan; names → via test_story_shots, test_story_refimages; prompting →
test_aistory_prompting; shots → test_story_shots.

### Nvidia link (Sonnet check)

`nvidia/nemotron-3.5-lightning-30b-a3b` at integrate.api.nvidia.com (`registry.py:81-117, 374`), key set, free, rpm 40,
`primary=False`, ~12 tok/s with ~50 s queueing, `structured=("json_schema",)`, thinking disabled for
nemotron-3.5-lightning/deepseek/glm (`llm.py:189-201`), no fallback_models; last in `DEFAULT_LLM_CHAIN`
(`registry.py:433-438`) and never reached because gemini answered 318/318 calls. OpenRouter link mistral-small-3.2
(`registry.py:413`, paid, DEC-206). `LLM_PRICES` has two rows (mistral-small-3.2, llama-3.3-70b).
