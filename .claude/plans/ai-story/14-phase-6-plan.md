# AI Story phase 6 — Tier 2/3 video, hosted I2V + local ComfyUI, the one_dollar planner, sticky links, paid LLM booking

Status: **APPROVED 2026-09-30**. Phase 5 closed with CI green (`f06a299`); stage 0 started 2026-09-30 from `main` `772a540`. Brief: `.claude/plans/ai-story/07-phase-6-video-tiers-local.md`.
Spec: `00-MASTER-SPEC.md` §2.8, §2.11, §8.1–8.7.

Planned in parallel with phase 5's close. Phase 5's session owns the live container and `main`'s `.claude/`
artifacts until then. On approval this file is copied to `~/.claude/plans/ai-story-phase-6-plan.md`. Stage 0
later commits it as `.claude/plans/ai-story/14-phase-6-plan.md` in the phase-6 worktree. **Nothing is implemented
before phase 5 is merged, deployed and acknowledged.**

## Reconciled with phase 5's start prompt (`13-phase-6-start-prompt.md`, read after approval)
- It lists the same core scope: paid LLM booking first, Tier 2/3, local ComfyUI.
- It names further follow-ups "only if the plan argues for them". This plan argues only for A-087. These stay
  follow-ups:
  - A-091 (the voice picker vs Gemini TTS);
  - A-092 (places pacing);
  - French scripts running short;
  - T1 under-shoot retries;
  - a CLI switch to use the stored Settings;
  - A-093 (the Edge catalogue check);
  - Hugging Face TTS.
- Stage 5 (paid LLM) is independent of video and can move up to stage 1 if the human wants "paid estimates first".

## Context

Phase 6 turns keyframes into clips when a story is set to `tier ≥ 2`. It is **keyframe-first**: the phase-4 shot
image is always the first frame, and a video model never invents a character from text.

It also closes three carried items:
- **A-087.** One image provider, and one video provider, per episode.
- **DEC-115's follow-up.** Paid LLM calls are estimated, capped and booked ("paid estimates end to end").
- **A-071.** Does fal bill a queued request that later fails?

## Human's answers (2026-09-30)

- **No GPU anywhere.** The VPS has 4 cores and 24 GB RAM and runs CPU-only (`container_no_gpu`). The human:
  "use providers I allow you". This replaces the first answer, which was a workstation GPU. So the local ComfyUI
  video path is built and proven only against a fake ComfyUI server. Its live run stays UNVERIFIED (A-035 open).
- **Paid walk on fal:** as briefed. One animated shot on the cheapest link, **hard cap $0.30**, estimate shown
  first.
- **Refusal shown under the default $1.00 per-episode cap.** Seedance prices a full episode at about
  $1.25–1.45, which a $1.50 story cap would not refuse.
- **A-071 probe:** one deliberately failing queued fal request, **≤ $0.05**, inside the $0.30.
- **Gemini Veo 3.1 Lite:** the adapter, plus **one live 4 s shot, own hard cap $0.25**. It uses a billing-enabled
  key from a separate project, `GEMINI_PAID_API_KEY`.
- **Nano-banana stays on `GOOGLE_API_KEY` for now.** Moving it is a follow-up. It is safe while that project keeps
  billing off.
- **Paid LLM booking + caps:** tests only. OpenRouter stays unfunded (the standing rule is kept).
- **A-087 → a sticky image and video link per episode**, with stop-and-ask on a switch.
- **Veo runs on a second story**, because one episode may use only one video link.
- **Start:** after phase 5 closes. Ids start at **DEC-200 / A-100** (agreed with the phase-5 session).

## Key findings the plan is built on (maps of 2026-09-30, file:line at `85b77a1`)

### Provider plumbing
Most of it exists and is kind-agnostic:
- The adapter contract: `generation.py:325-344`.
- The runner and paid gate: `_run_candidates` `:452-518`.
- The journal, resume and conservative booking: `gencache.py:152-167`, `_resume` `:652-701`.
- `FalAdapter` submit/poll/resume: `images.py:222-328`. `FAL_APPS` already lists the three video app ids.
- `ComfyUIClient`, `render_template` and `validate_template`: `local_comfyui.py`. `TYPED` already has
  frames/fps.
- Per-second video prices: `pricing.py:43-46`.

What is missing:
- Any `(VIDEO, *)` adapter.
- Video in `CACHED_KINDS` (`gencache.py:66-67`).
- Video templates.
- `/free`.
- A Veo long-running-operation client.

### Credentials
Credentials are per provider. Gemini's `env_keys=("GOOGLE_API_KEY",)` (`generation.py:107`) serves both free and
paid links, so a Veo call would bill the free chain's project. Veo needs a per-link key.

### The LLM seam needs no edit to `llm.py`
`run_chain(client_factory=…)` (`llm.py:366-377`) is threaded down to `LlmClient._factory` (`:238`), and the story
wrapper `steps/llm_call.py` calls it (`:273`). A metering factory therefore sees every reply and the link that
answered. RC-S4 is kept.

### Budget and the CLI
- The budget is the merged environment (`gating.budget_of`, `budget.py:84`).
- The CLI's `settings_env={}` (`cli.py:600`, DEC-114) means a CLI process sees only its own environment.
- So paid runs get `ALLOW_PAID` and caps **per process**, and Settings' `allow_paid` stays off.

### "Test chain"
Settings "Test chain" spends once on a pressed paid link (DEC-103, `routes/settings.py:662-707`). For video that
would buy a clip.

### Story side
- `generation_profile.tier` is validated but read nowhere.
- `keep_still` works and is read by `render/plan.py:338`. Patching it through the storyboard clears the
  storyboard approval (`workflow.py:3176-3196`); `patch_assets` (`:3242-3287`) does not.
- `video_prompt` and `assets.video` are reserved but never written.
- The `budget_profiles.json` fields `animate` and `animate_priority` are validated but never read, so the planner
  does not exist.
- `assets_fingerprint` hashes explicit fields (`assets.py:339-360`). A clip part added only when present keeps
  stored episodes byte-identical.

### Renderer
- The video branch exists (`render/plan.py:336-341`), but `render_inputs` never fills `videos`
  (`steps/render.py:199-243`).
- `tier2_clip_argv` (`filtergraph.py:181-210`) has never run on a real clip and does not hold a short clip's last
  frame. It is golden-string-pinned at `test_aistory_render_filtergraph.py:398`.

### Other constraints
- `data/usage.json` resets daily (`limits.py:95-101`), so it cannot hold an ETA history.
- The busy rule is one job per story (`stories.py:373/451`), so animating N shots is one job.

### Tests that change on purpose
Each edit is named in the action log:
- `test_generation_chain.py:256-261`
- `test_image_adapters.py:70`
- `test_generation_chain_api.py:259-263`
- `test_stories_api.py:706,1053`
- `test_story_workflow.py:302`
- `test_story_workflow_episode.py:134-152`
- `test_stories_api_phase4.py:136,337,555`: the `:frames` case stays `later_phase`.
- `test_aistory_render_filtergraph.py:398`

## Stages

### Rules for every stage
- Each stage ends: Tier-1 once, run by me, in both environments, with xdist (DEC-176) → compileall → a vite build
  when the dashboard changed → commit (explicit paths, no trailers) → one log line → one CHECKPOINT row.
- Essential tests only: one fail-first test per behaviour plus one guard of the working path. Tests use stdlib and
  pytest only (DEC-012).
- Agents run only their new and touched tests.
- Model tier in brackets.
- Rollback = revert the stage's commit. Nothing is live until the stage-13 deploy.
- From phase 5's start prompt (`13-phase-6-start-prompt.md`):
  - never `git stash`;
  - never revert files with `git checkout` for a fail-first check;
  - no phase-6 merge or push while `main`'s CI is red;
  - keep every story (deleting one is the human's call);
  - A-096: before any push that adds a test depending on an optional package (PIL, fastapi), run the CI-faithful
    replica: the app image's Python 3.11, `python -S`, pytest only.

### Stage 0 — checkpoint, worktree, baseline [inline]
- **Starts only when phase 5 is closed.**
- **Worktree.** Create `.claude/worktrees/ai-story-phase-6` on `feat/ai-story-phase-6` from `main`, and symlink
  `node_modules`.
- **Baseline.** Tier-1 in both environments.
- **Backups.** Tars with sha256 of stories `979c8376e43e` and `04feb539840f`, `data/spend.json` and
  `data/usage.json`.
- **CHECKPOINT.** Header with the hash, the regression contract and this plan, committed at
  `.claude/plans/ai-story/14-phase-6-plan.md`.
- **Risk:** none. **Rollback:** delete the worktree.

### Stage 1 — pure video planning [Sonnet]
New `clipping/aistory/video_plan.py`:
- **`CAMERA_PHRASES`.** Exactly the closed list `CAMERA_MOTIONS` (`schemas.py:145`) mapped to phrases.
- **`build_video_prompt`.**
  - The prompt is the action sentence, then `motion_rules.tier2_prompt_suffix`, then the camera phrase.
  - At tier 3 it adds the shot's line text.
  - The negative is the style's negative plus a fixed motion negative.
- **`CLIP_LENGTHS` per link.**
  - seedance: 2–12 s
  - ltx-2-fast: 6/8/10 s
  - kling: 5/10 s
  - veo: 4/6/8 s
  - local templates: their frame rule
- **`requested_seconds`.** The smallest supported length that is ≥ `duration_s`, else the longest. The render then
  holds the last frame.
- **`plan_animation`**, a pure greedy planner.
  - Order: pinned shots first, then `animate_priority` (hook → cliffhanger → peak → turn), then the longest
    dialogue. Ties go to shot order.
  - `keep_still` shots are excluded.
  - Current clips cost $0.
  - It returns the image/video split against what is left of the per-episode cap.
- **`video_route`.** auto, local or api, decided from the probe results passed in.
- **Tests:** `tests/test_story_video_plan.py`, fail-first because the module is missing. It covers the phrase
  table against the closed list, the rounding table, planner fixtures and the route decision.
- **Risk:** low.

### Stage 2 — provider foundations [Opus: payment]
- **gencache.** `"video"` joins `CACHED_KINDS` and `SEEDED_KINDS`.
  - Only when `kind == "video"`, the payload adds `clip_s`, `fps` and `native_audio`; the keyframe sha comes
    through `refs`.
  - `KEY_VERSION` stays 1.
- **Paid Gemini key.**
  - Add `generation.LINK_ENV_KEYS = {"gemini/veo-3.1-lite": ("GEMINI_PAID_API_KEY",)}` and
    `env_keys_for(link)`.
  - Use them in `missing_keys`, `credentials_for` and `gating.link_summary`.
  - Add the key to `PERSISTED_KEYS` and `SECRET_KEYS`.
- **Prices.** Re-verify the four video prices on the provider pages and bump `PRICES_AS_OF`. One A-entry per
  model id.
- **Tests (fail-first):**
  - the video key exists and moves with `clip_s` and the keyframe;
  - Veo with only `GOOGLE_API_KEY` set is skipped as "GEMINI_PAID_API_KEY is not set".
- **Guard:** the image and TTS key hexes, pinned from the pre-change code.
- **Risk:** medium (cache keys). **RC at risk:** RC-A2, RC-A3, RC-V2, RC-V4.

### Stage 3 — hosted video adapters `clipping/providers/video.py` [Opus: payment]
- **`FalVideoAdapter(images.FalAdapter)`.**
  - Per-model `_inputs`: the keyframe, the duration, 720p, 9:16, and the negative where the model takes one.
    ltx-2 gets `generate_audio` only for native audio.
  - `_fetch` reads `video.url`.
  - The only edit in `images.py` is a `poll_budget_seconds` class attribute; images keep 300 s.
  - `resume` never resubmits (DEC-152).
- **`GeminiVeoAdapter`.**
  - REST `predictLongRunning` with `GEMINI_PAID_API_KEY`. The operation name is journaled at submit.
  - It polls the operation, then downloads the video. `resume` polls the same operation.
  - An operation that ends in error raises `RequestFailed` and stays booked.
- **Estimates.** Per second: `pricing.estimate(link, clip_s)`.
- **Wiring.** Register the adapters in `adapters.load_all`, and add the Veo id to `api_model_id`.
- **Tests (fake transport):**
  - one submit per request, journaled;
  - poll, then fetch, then an `.mp4`;
  - a FAILED request ends `failed` and stays booked;
  - resume never resubmits;
  - the estimate is seconds × price.
- **Deliberate re-pins:** `test_generation_chain.py:256-261`, `test_image_adapters.py:70`.
- **RC at risk:** RC-A3, RC-V3.

### Stage 4 — local ComfyUI video [Opus; the brief's riskiest; fake server only]
- **Templates.** `i2v_wan22_5b`, `i2v_wan22_14b_lightning` and `i2v_ltx2`.
  - Format `comfy_workflow_v1`, with the §8.7 placeholders, `requires` (model files) and a frame rule.
  - Authored from ComfyUI's published default graphs.
- **`ComfyUIVideoAdapter`.**
  - It validates against `object_info` before queueing; anything missing gives an install message naming the
    files.
  - It uploads the keyframe and forwards `/ws` progress to `on_log`, and from there to the job activity and SSE.
  - It reads the `videos`/`gifs` outputs.
  - `resume(prompt_id)` resumes a journaled prompt.
  - A new client call `free()` (`POST /free`).
- **Hardware.** `hardware.VIDEO_WORKFLOWS = {mid: wan22_5b, high: wan22_14b_lightning, pro: ltx2}`. The profile
  comes from ComfyUI's `/system_stats` when it is reachable.
- **Settings "Test chain" for video never generates.**
  - A local link is checked with `object_info` only.
  - A hosted link shows its key and estimate only.
  - This amends DEC-103 for video; re-pin `test_generation_chain_api.py:259-263`.
- **Tests:**
  - placeholder injection with frames and fps as ints;
  - a missing model file named in the message (fail-first);
  - `/free` only when asked;
  - `/ws` progress lines;
  - resume by prompt id.
- **Risk:** high, because the code is never run live (A-035). **RC at risk:** RC-V8.

### Stage 5 — paid LLM booking seam [Opus: payment]
Applies in `steps/llm_call.py` only when `allow_paid` is on and the usable chain has a paid link.
- **Before the call.** For each paid link:
  - estimate (tokens of system + user + the cap) × its token price;
  - run `budget.check` for the episode, day and story.
  - A refused link is skipped with the numbers printed, and free links still run.
- **Metering.** `client_factory=` gets a metering wrapper over `llm.build_client`.
  - Each paid reply is booked as unit `token`, with the cost taken from its usage.
  - A reply with no usage is booked at the estimate, with a DEC-153 note.
- **Prices.** `pricing.LLM_PRICES` holds verified prices for the paid links of `DEFAULT_LLM_CHAIN`. A link with no
  price is refused, never guessed.
- **Tests:**
  - a paid reply is booked from its usage (fail-first);
  - over a cap, the paid link is skipped and a free one answers;
  - **guard:** the free path passes exactly today's keyword arguments.
- **Unchanged:** `git diff 25abdd1 -- clipping/providers/llm.py` stays empty.
- **RC at risk:** RC-S4, RC-P5, RC-M7. Independent of video.

### Stage 6 — sticky image link per episode, A-087 [Opus: data mutation]
- **Record.** `assets.json` gets an optional `links {image|video: {link, since, switched_from?}}`.
  - It is written after the first image a link serves.
  - When there is no record, it is derived only if every current image shares one link.
  - A legacy mixed episode behaves as today, with one printed note.
- **With a record.** The runner gets a one-link chain. `FALLBACK_LINKS` retired-model swaps still apply. A
  rate-limited link is waited on through DEC-168's paced rounds.
- **Link gone for the day.** That is: no key, allowance spent, `allow_paid` or a cap refuses, a 401/403, or a local
  server unreachable.
  - `plan_refusal` stops before any call with `StickyLinkGone`, in the DEC-117 shape: the link, why, the next
    runnable link, the shots to redo, and the estimate.
  - If it happens mid-run, the remaining shots fail with the same message.
- **Switch.** Only by `patch_assets {"links": {"image": …}}`. Images made on the old link go `stale`, and the next
  run redoes exactly those.
- **Tests:**
  - the second shot never falls through (fail-first);
  - a gone link stops before any call with the offer (fail-first);
  - the switch stales only the served shots;
  - **guard:** an episode with no record is unchanged (the pacing tests stay unedited).
- **RC at risk:** RC-V5, the DEC-168 paths.

### Stage 7 — clip documents and the estimate, no calls [Opus: data mutation]
- **Optional schema fields.**
  - Shot `animate` (a pin) and `keep_native_audio`.
  - `assets.clip {state current|stale|failed, link, route, clip_s, est_usd, prompt_hash, image_sha256, cache_key,
    generated_at, note, pending, reason}`.
  - `assets.video` is set only for a current clip.
  - `SHOT_CLIP_NAME_PATTERN` `^shot_NN\.mp4$` and a store kind `clips`.
- **Fingerprint.** Gains a `clips` part only when a clip, a pin, or tier ≥ 2 is present.
- **`patch_assets`.** Accepts `keep_still`, `animate` and `keep_native_audio`. The storyboard approval never moves.
- **`asset_units`.** Gains `video {route_class, link, plan[{shot_id, clip_s, est_usd, why}], still[], seconds,
  est_usd, eta_s|null, ready, message}`.
  - The total includes it, and `over_cap` names the numbers.
  - `fast_track.paid_verdict` counts it.
- **`data/gen_timings.json`** (`gen_timings_v1`).
  - Holds the last 20 `{wall_s, clip_s}` per link, template and profile.
  - The ETA is the median rate × the planned seconds.
  - With no history the ETA is null, shown as "no measured history".
- **Tests:**
  - the estimate includes seconds × price (fail-first);
  - **guards:** a tier-1 estimate is byte-identical, and a stored fixture's fingerprint is unchanged.
- **RC at risk:** RC-V1, RC-M3.

### Stage 8 — the video phase and `shot:<ep>:<shid>:video` [Opus: payment + data mutation] — RISKIEST
- **When it runs.** At tier ≥ 2 with the assets param `animate` (default on). It runs after images, voices and the
  paced round.
- **`/free`.** Called once when a local image ran and the profile has ≤ 12 GB.
- **Per planned shot, in plan order:**
  - the keyframe must be current;
  - the seed is derived from the shot seed;
  - the sticky video link applies;
  - the cache books `(video, second, clip_s)`;
  - the clip is copied atomically to `assets/clips/shot_NN.mp4`;
  - on failure: `clip.state=failed` with the reason and the hop printed. There is never another link.
- **Poll timeout.** The request stays `submitted`, and Continue resumes it (DEC-152).
- **Regenerate `shot:<ep>:<shid>:video`.** A note gets a fresh seed and becomes `clip.pending` (DEC-154). Other
  `shot:…` forms stay `later_phase`. Re-pin the named tests.
- **Tests:**
  - the run animates exactly the estimate's list (fail-first);
  - a failed clip is recorded as failed, with no fallback (fail-first);
  - one submit per paid clip, and a rerun with the same inputs costs $0;
  - **guard:** a tier-1 run makes 0 video calls and its documents are byte-identical.
- **RC at risk:** RC-V1, RC-V3, RC-V5, RC-V6, RC-M7.

### Stage 9 — renderer [Opus: cross-cutting]
- **Inputs.** `render_inputs` fills `videos` with the current clips of shots that are not `keep_still`.
- **Failed or stale clips.** A planned shot whose clip is failed or stale is refused, naming the shots, unless the
  render param `fill_failed_with_motion` is set (default off, recorded only when true). With it, the shot renders
  with Tier-1 motion.
- **Hold.** `tier2_clip_argv` gets `tpad=stop_mode=clone` before `trim`; re-pin `:398`.
- **Manifest.** Optional `shot_modes {id: video|motion|motion_keep_still|motion_fill}`, written only when some shot
  is not plain motion.
- **Tier-2 golden.** A sibling builder: a 1.0 s lavfi clip, which exercises the hold, plus one still, recorded in
  `tests/fixtures/aistory_golden_tier2/framemd5.json`. The host and container keys are recorded now; the CI key on
  the first push. `framemd5.json` stays unedited.
- **Tests (fail-first):**
  - the clip is used;
  - a failed clip is refused without the param;
  - with the param, the shot gets motion and its label.
- **RC at risk:** RC-M2, RC-M3, RC-M8, RC-V7.

### Stage 10 — Tier-3 native audio [Opus; tests only]
- **When.** At tier 3, only for shots with `keep_native_audio`.
- **Mix.** The clip's audio becomes one more stem on the absolute timeline: `adelay` to the shot's start, 10 ms
  edge fades, trimmed to the shot.
  - The shot's own lines are not placed.
  - Subtitles still come from our line text and TTS timing.
  - The ducking is unchanged, and the video stage keeps `-an`.
- **Tests:**
  - the stem sits at the right offset and the shot's lines are absent (fail-first, synthetic sine clip);
  - **guard:** a tier-2 clip's audio is discarded.
- **Risk:** medium, and never verified live (budget).

### Stage 11 — API and CLI [Sonnet]
- **Params.** `AssetsStepParams.animate`; `RenderStepParams.fill_failed_with_motion`.
- **Routes.**
  - `GET /estimate/assets?route=` prices another route without patching the story.
  - `GET /episodes/{ep}/clips/{name}` is the twin of the shot-image route: fetched as a blob (DEC-113), open when
    `API_TOKEN` is unset (DEC-173).
  - The episode view shows each shot's clip state and blocked reasons, in the F8 pattern.
- **CLI.**
  - `step assets --tier N --route R` patches the story's `generation_profile` and prints it. There is no run-level
    override.
  - `--no-animate`.
  - `--estimate` prints `asset_units` and calls nothing.
  - `render --fill-failed-with-motion`.
- **Tests:**
  - the flags reach the params;
  - `--estimate` makes no call;
  - the clip route answers 404 outside its folder;
  - a defaults agreement test.
- **RC at risk:** RC-M9, RC-S2.

### Stage 12 — dashboard [Sonnet]
- **Story page:** the tier control plus the next-episode estimate per route.
- **Shot cards:**
  - animate, keep still, and re-animate with a note;
  - clip preview, clip-state and route badges, the local ETA or the API cost;
  - `EstimateChip` learns video seconds.
- **Offers and settings:**
  - the sticky-switch offer, behind a confirmation;
  - the render checkbox "fill failed shots with motion";
  - the `GEMINI_PAID_API_KEY` badge and the video workflow rows.
- **Checks:** 375, 820 and 1280 px on scratch servers. There is never a sign-in screen.
- **RC at risk:** RC-M9.

### Stage 13 — deploy and the Tier-2 live walk [Opus: paid]
The script is below. The deploy is `rm -sfv` + `up -d --build` at 0 jobs.

### Stage 14 — docs and decisions [Sonnet]
- **`docs/AI_STORY.md`:**
  - the tiers;
  - the local setup guide per profile, with the workflows and model downloads;
  - Docker and the host ComfyUI;
  - the planner and sticky links;
  - LoRA as a future extension, with the fal and local cost numbers.
- **`.claude/` artifacts:**
  - VISION;
  - DEC-200+ and A-100+;
  - the pricing table;
  - the CHECKPOINT close;
  - follow-ups: nano-banana → paid key, a live local run when a GPU exists.

## Regression contract (phase 6)

| ID | Must keep working | Proven by |
|---|---|---|
| RC-V1 | A tier-1 story's estimate, assets and render are byte-identical, and it makes no video call | stage 7/8 guards + the walk's re-render sha of an untouched phase-5 episode |
| RC-V2 | Image and TTS cache keys are unchanged | stage-2 pinned-hex guard |
| RC-V3 | No paid clip or LLM call without `allow_paid` and the caps; one submit per clip; every billed call booked (seconds or tokens) | stage 3/5/8 tests |
| RC-V4 | The free Gemini chain never reads `GEMINI_PAID_API_KEY`; Veo never reads `GOOGLE_API_KEY` | stage 2 |
| RC-V5 | No silent mixing of image or video links inside an episode | stage 6/8 |
| RC-V6 | The estimate and the run agree on the shots and the dollars | stage 8 |
| RC-V7 | A failed or stale clip never renders unless "fill" is ticked | stage 9 |
| RC-V8 | Settings "Test chain" never buys a clip | stage 4 |

Carried unchanged: RC-M2 (golden `framemd5.json` unedited), RC-M3, RC-M6 (clip mode), RC-M7, RC-M8, RC-M9 (no
auth), RC-A2, RC-A3, RC-P5, RC-S4 (`llm.py` untouched).

## Expected decisions and assumptions

**Decisions:**
- DEC-200: keyframe-first I2V.
- DEC-201, verbatim: "Native model audio is discarded at Tier 2 — dialogue comes from our TTS for voice
  consistency — and kept only under the Tier-3 opt-in".
- DEC-202: video is the last phase of the assets step (the `animate` param).
- DEC-203: the planner's selection is derived, never stored; swaps go through `patch_assets`; the `free` profile
  animates only at $0.
- DEC-204: sticky image and video links per episode (amends DEC-168's fall-through).
- DEC-205: `GEMINI_PAID_API_KEY`.
- DEC-206: paid LLM metered and booked (amends DEC-115).
- DEC-207: the video cache key.
- DEC-208: the clip-length table; trim or hold at render.
- DEC-209: `fill_failed_with_motion` is a render param, default off.
- DEC-210: Tier-3 audio stem (amends DEC-158).
- DEC-211: `gen_timings.json`.
- DEC-212: the video "Test chain" never generates (amends DEC-103).
- DEC-213: CLI `--tier`/`--route` patch the story.
- DEC-214: `PRICES_AS_OF`.
- DEC-215: paid walks use per-process caps.

**Assumptions:**
- A-100+: one per video model id and price.
- Local templates unverified live (A-035 open).
- Tier-3 lip-sync mismatch expected.
- The A-071 outcome.
- fal and Veo latency and billing lag.
- Whether Veo bills audio.

## Rejected alternatives

- **A separate `animate` step or fast-track sub-step.** It would add a step, gate, estimate and CLI path, and
  re-pin `SUB_STEPS`. It gains no parallelism, because the busy rule allows one job per story, and the `animate`
  param already lets the keyframes be reviewed first.
- **Storing the planner's selection.** It goes stale when prices, spend or caps move. A derived selection keeps
  the estimate equal to the run.
- **A run-level `--tier` on the CLI.** The dashboard and the estimate would disagree with what ran. There is no
  per-episode tier.
- **The ETA in `usage.json`.** That file resets daily.
- **Editing `llm.py` to return usage.** It breaks RC-S4, and the factory seam exists.
- **Per-shot `acrossfade` for Tier 3.** It breaks DEC-158's arithmetic.
- **A live local run on a rented GPU.** The human chose no GPU.

## DECISIONS check

- **Amended:**
  - DEC-102: more than adapter registration.
  - DEC-103: the video test never generates.
  - DEC-115: paid LLM booked.
  - DEC-158: the Tier-3 stem.
  - DEC-168: sticky links wait instead of falling through.
- **Reused in shape:**
  - DEC-117: stop-and-ask.
  - DEC-152/153: resume and conservative booking.
  - DEC-154: pending seed.
  - Phase 5's "a stale asset is never rendered".
- **Respected unchanged:**
  - DEC-112: route per story; the per-task selector stays a follow-up.
  - DEC-114: CLI environment.
  - DEC-173: no auth.
  - DEC-174: capped paid-test pattern.
  - DEC-176: test policy.
  - Phase 5's whole-frame and partial re-render decisions.
  - DEC-003/023: a chain is never reordered.

No other conflicts. Re-checked at stage 0 against phase 5's final DEC-177…194: no conflicts.
- DEC-190's cast pacing is unaffected: stickiness applies to episode shots only.
- DEC-194's capped-test pattern (the daily cap is the hard limit; episode and story caps = held + headroom) is
  what DEC-215's per-process caps reuse.

## Riskiest stage

**Stage 8.** It is the only stage that both spends money and mutates episodes. A bug could buy a clip twice, mix
links silently, or let the estimate and the run diverge. It is guarded by the fail-first plan == run and
no-fallback tests, plus one-submit and $0-rerun.

Stage 4 is second: code that is never run live (A-035).

## Tier-2 live walk (stage 13, after the deploy at 0 jobs)

Settings' `allow_paid` stays **off** throughout. Each paid run is a CLI process in the container that gets
`ALLOW_PAID` and its caps as that process's environment, with `LLM_CHAIN` pinned to free links. Nothing is
persisted.

1. **Preconditions.**
   - The human pastes `GEMINI_PAID_API_KEY` in Settings. It comes from a separate billing-enabled project, and I
     never see the value.
   - The human confirms the `GOOGLE_API_KEY` project has billing off.
   - Check the backups' sha256.
2. **Story A (cartoon_flat `979c8376e43e`, EN, episode 1, $0 booked).**
   - Set tier 2 and route `api`.
   - At 375 px the estimate shows seconds × price and "allow_paid is off".
3. **Refusal ($0).**
   - Full episode on seedance with `ALLOW_PAID=1` and `PER_EPISODE_CAP_USD=1.00`.
   - `--estimate` shows about $1.25–1.45, then `step assets` is refused before any call, with both numbers.
   - The ledger is unchanged.
4. **STOP.** I show one hook shot on seedance: its `clip_s`, its estimate, and caps of $0.30 (episode, story, and
   daily = today's spend + 0.30). The human says go.
5. **fal run.**
   - Every other shot is keep-still.
   - Record the request id, the journal `submitted → done`, and the ledger row (seconds + cost).
6. **A-071.**
   - If no failure happened naturally, **STOP**, then one 2 s request whose keyframe fails after queueing
     (≤ $0.05, inside the $0.30).
   - It is booked conservatively; later compare it with fal's request list and billing.
7. **Sticky check ($0).** `VIDEO_CHAIN=gemini/veo-3.1-lite` with one more shot un-kept. It stops before any call,
   naming seedance and the switch offer. Revert the shot.
8. **Render (free, dashboard).** The manifest `shot_modes` shows 1 video and the rest keep-still. The ledger view
   shows seconds and cost.
9. **Story B (claymation `04feb539840f`, FR, episode 1).**
   - **STOP:** I show the Veo plan: 4 s, about $0.20, cap $0.25. The human says go.
   - Run it with `VIDEO_CHAIN` pinned to Veo and caps of spent + $0.25. Journal the operation name.
   - Render the mixed episode.
10. **Billing.** Compare the ledger with fal's dashboard and Google billing (Google's may lag; recheck later).
11. **RC-V1/RC-M3.** Re-render an untouched phase-5 episode and check its sha is identical.
12. **STOP.** The human watches both mixed episodes on the phone at 375 px and acknowledges the substitute.
    - The local ComfyUI path is not run and is UNVERIFIED.
    - Tier 3 is proven by tests only.

**Total paid ceiling: $0.55** ($0.30 fal including the A-071 probe, plus $0.25 Veo).

## Verification (end to end)

- Tier-1 green in both environments at every stage.
- Fail-first shown for:
  - the video prompt builder;
  - the duration table;
  - the route decision;
  - the estimate including seconds;
  - placeholder injection and `object_info` validation;
  - the failed-shot policy;
  - the sticky link;
  - LLM booking.
- Acceptance (the brief's):
  - a mixed Tier-1/Tier-2 episode renders and its manifest labels each shot;
  - the ledger shows seconds and cost;
  - refusals carry the numbers;
  - the human acknowledges the walk.

## Critical files

- `clipping/providers/`: `generation.py`, `gencache.py`, `images.py` (one attribute), new `video.py`,
  `local_comfyui.py`, `pricing.py`, `hardware.py` (aistory), `adapters.py`.
- `clipping/aistory/`: new `video_plan.py`, `steps/assets.py`, `steps/llm_call.py`, `steps/render.py`,
  `steps/fast_track.py`, `render/filtergraph.py`, `render/plan.py`, `render/manifest.py`, `schemas.py`,
  `store.py`, `workflow.py`, `cli.py`, `templates/workflows/*/i2v_*.json`.
- `web/api/`: `routes/stories.py`, `models.py`, `routes/settings.py`, `settings_store.py`.
- `web/dashboard/src/`: `pages/story/StoryboardPane.jsx`, `PreviewPane.jsx`, `components/EstimateChip.jsx`,
  `Settings.jsx`.
